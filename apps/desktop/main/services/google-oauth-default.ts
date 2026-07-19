import { readFileSync } from "node:fs";
import { join } from "node:path";

// Bundled-default Google OAuth client for the packaged app.
//
// Google's native-app model (developers.google.com/identity/protocols/oauth2/
// native-app) is: the DEVELOPER registers ONE "Desktop app" OAuth client and
// embeds it in the app — no per-user setup, PKCE secures the exchange. So the
// distributed CLI/desktop ships a default client so "Continue with Google"
// works out of the box for every npm user.
//
// The credentials must NOT live in git (this repo is public). Instead a
// gitignored `google-oauth.json` sits next to the app (app.getAppPath()):
//   • in a dev checkout: apps/desktop/google-oauth.json
//   • in the published payload: payload/desktop/google-oauth.json — written by
//     tools/cli/scripts/assemble-payload.mjs at prepack from that same file OR
//     from GOOGLE_OAUTH_CLIENT_ID/SECRET in the publish (CI) env.
// git never sees it; the npm tarball does.
//
// An operator/self-host env var ALWAYS wins over the bundled default: if
// GOOGLE_OAUTH_CLIENT_ID is already set we leave the env untouched. This is the
// single seam that turns the default on — the confidential/public decision
// stays single-sourced in the backend's build_google_provider.

export const BUNDLED_GOOGLE_OAUTH_FILE = "google-oauth.json";

const ENV_CLIENT_ID = "GOOGLE_OAUTH_CLIENT_ID";
const ENV_CLIENT_SECRET = "GOOGLE_OAUTH_CLIENT_SECRET";

/** Shape of the bundled `google-oauth.json`. */
export interface BundledGoogleOAuth {
  readonly client_id?: string;
  readonly client_secret?: string;
}

export interface ApplyGoogleOAuthResult {
  /**
   * "env"     — an operator env var was already set; nothing changed.
   * "bundled" — the bundled default file supplied the client id (+ secret).
   * "none"    — no env var and no usable bundled file; Google stays disabled.
   */
  readonly applied: "env" | "bundled" | "none";
}

export interface ApplyGoogleOAuthDeps {
  /** Injectable file read for tests; defaults to fs.readFileSync(utf8). */
  readonly readFile?: (path: string) => string;
}

/**
 * Seed `env.GOOGLE_OAUTH_CLIENT_ID` (+ `_SECRET`) from the bundled default when
 * the operator has not supplied their own. Mutates `env` in place so the
 * existing service-env passthrough forwards the values to the backend child.
 * Never overwrites an operator-provided client id.
 */
export function applyBundledGoogleOAuth(
  env: Record<string, string | undefined>,
  appPath: string,
  deps: ApplyGoogleOAuthDeps = {},
): ApplyGoogleOAuthResult {
  // Operator / self-host env override always wins.
  if ((env[ENV_CLIENT_ID] ?? "").trim() !== "") {
    return { applied: "env" };
  }

  const read = deps.readFile ?? ((path: string) => readFileSync(path, "utf8"));

  let parsed: BundledGoogleOAuth;
  try {
    parsed = JSON.parse(
      read(join(appPath, BUNDLED_GOOGLE_OAUTH_FILE)),
    ) as BundledGoogleOAuth;
  } catch {
    // Missing file (a fork stripped it) or malformed JSON — Google stays off.
    return { applied: "none" };
  }

  const clientId = (parsed.client_id ?? "").trim();
  if (clientId === "") {
    return { applied: "none" };
  }
  env[ENV_CLIENT_ID] = clientId;

  const clientSecret = (parsed.client_secret ?? "").trim();
  if (clientSecret !== "") {
    env[ENV_CLIENT_SECRET] = clientSecret;
  }
  return { applied: "bundled" };
}
