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
//
// Single authoritative signal: production posture also folds in the *supervise*
// decision (`shouldSupervise`). A supervised local stack is ALWAYS
// production-configured — service-env.ts pins every child to
// `*_ENVIRONMENT=production`, so the dev IdP mint route (`/v1/dev/identity/mint`)
// is never registered there. Deriving both the supervise decision and the auth
// posture from the same `shouldSupervise` predicate makes them impossible to
// contradict. Before this, they keyed off *different* signals
// (supervise ← `isPackaged || COPILOT_RUNTIME_DIR`; posture ←
// `isPackaged || COPILOT_PRODUCTION`), and the documented supervised dev recipe
// (`COPILOT_RUNTIME_DIR=… npm run dev`, which sets no COPILOT_PRODUCTION)
// supervised a production stack yet resolved DEV posture — so the default
// "Sign in (local)" button routed to dev-mint and could never authenticate
// against its own supervised stack.

import type { AuthMode } from "./auth/oidc-client";
import { shouldSupervise } from "./services/boot-mode";

export interface PostureInputs {
  readonly isPackaged: boolean;
  readonly env: Readonly<Record<string, string | undefined>>;
}

// True when the app is a real end-user install and must run real sign-in +
// fail closed on stale sessions. Explicit dev overrides win so the monorepo dev
// flow (`npm run dev`) keeps dev-mint and the local-only option:
//   COPILOT_AUTH_MODE=dev-mint  or  COPILOT_DEV=1
// Absent an explicit dev override, the app is in production posture whenever it
// (a) supervises a local stack — packaged, or a staged `COPILOT_RUNTIME_DIR`,
// both of which run production-configured children — or (b) is explicitly told
// so via COPILOT_PRODUCTION=1 (the CLI's signal, and the way to force real
// sign-in against an external facade with no local supervisor).
export function isProductionPosture(inputs: PostureInputs): boolean {
  if (inputs.env.COPILOT_AUTH_MODE === "dev-mint") return false;
  if (inputs.env.COPILOT_DEV === "1") return false;
  return shouldSupervise(inputs) || inputs.env.COPILOT_PRODUCTION === "1";
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
