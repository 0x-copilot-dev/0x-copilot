// Production vs dev posture. Pure helpers so they are unit-testable without
// loading the electron module graph (mirrors services/boot-mode.ts).
//
// Why this exists: `app.isPackaged` is the usual "am I a real install?" signal,
// but the `copilot` CLI launches Electron pointed at a directory
// (tools/cli/lib/launch.mjs `spawn(electron, [appDir])`), so `app.isPackaged`
// is FALSE for a genuine end-user install. Auth must not key its dev-mint /
// fail-closed behavior off `app.isPackaged` alone — it would drop a real
// install into DEV posture and mint the "Sarah Chen" dev persona. The CLI sets
// COPILOT_PRODUCTION=1 as a trustworthy production signal; this helper folds it
// in alongside `app.isPackaged`.

import type { AuthMode } from "./auth/oidc-client";

export interface PostureInputs {
  readonly isPackaged: boolean;
  readonly env: Readonly<Record<string, string | undefined>>;
}

// True when the app is a real end-user install and must run real sign-in +
// fail closed on stale sessions. Explicit dev overrides win so the monorepo dev
// flow (`npm run dev`) keeps dev-mint and the local-only option:
//   COPILOT_AUTH_MODE=dev-mint  or  COPILOT_DEV=1
export function isProductionPosture(inputs: PostureInputs): boolean {
  if (inputs.env.COPILOT_AUTH_MODE === "dev-mint") return false;
  if (inputs.env.COPILOT_DEV === "1") return false;
  return inputs.isPackaged || inputs.env.COPILOT_PRODUCTION === "1";
}

export interface AuthPosture {
  /** Real install: no dev-mint, fail closed. */
  readonly productionPosture: boolean;
  /** Mode handed to OidcClient. Never "dev-mint" in production posture. */
  readonly mode: AuthMode;
  /** Whether the "Use locally, no account" dev-mint path is offered/allowed. */
  readonly allowDevMint: boolean;
}

// Resolves the auth posture from the same inputs. In production posture the
// mode is forced to "oidc" so OidcClient.signIn()/refresh() can NEVER mint a
// dev persona — the wallet + Google flows are mode-independent and stay
// available (they bypass OidcClient entirely). "oidc" here does NOT imply a
// configured OIDC provider: buildAuthService only validates/builds the OIDC
// provider config when COPILOT_AUTH_MODE=oidc was explicitly requested.
export function resolveAuthPosture(inputs: PostureInputs): AuthPosture {
  const productionPosture = isProductionPosture(inputs);
  const explicitOidc = inputs.env.COPILOT_AUTH_MODE === "oidc";
  const mode: AuthMode =
    explicitOidc || productionPosture ? "oidc" : "dev-mint";
  return { productionPosture, mode, allowDevMint: !productionPosture };
}
