import type { AuthSession } from "./oidc-client";
import { awaitLoopbackCode, type LoopbackHandle } from "./loopback-server";
import { fetchProfileClaims } from "./profile-claims";

// "Continue with Google" — facade-brokered OIDC (backend flow, branch
// feat/google-login-backend). Unlike OidcClient's direct-IdP mode, PKCE
// verifier + nonce live SERVER-side, bound to the single-use `state` row:
//
//   1. bind an ephemeral loopback server (random port, conflict retry)
//   2. GET {facade}/v1/auth/oidc/google/start?redirect_uri=<loopback>&
//      format=json  →  { auth_url, state, expires_at }
//   3. arm the loopback with `state`, open `auth_url` in the system browser
//   4. Google redirects the browser to the loopback with ?state&code
//   5. GET {facade}/v1/auth/oidc/callback?state&code  →  JSON handoff
//      { user_id, session_id, bearer_token, expires_at, requires_mfa, … }
//   6. best-effort GET /v1/me/profile with the new bearer to fill the
//      renderer-facing display claims (the handoff carries ids only)
//
// The bearer never leaves the main process; callers persist the returned
// AuthSession via SecretStorage exactly like the other sign-in modes.

const DEFAULT_TIMEOUT_MS = 5 * 60 * 1000;
const CALLBACK_PATH = "/oidc/cb";
const DEFAULT_SESSION_TTL_MS = 60 * 60 * 1000;

export interface GoogleLoginDeps {
  readonly facadeBaseUrl: string;
  readonly openExternal: (url: string) => Promise<void>;
  readonly fetch?: typeof fetch;
  readonly loopback?: typeof awaitLoopbackCode;
  readonly clock?: () => number;
  readonly timeoutMs?: number;
  /** Opaque `return_to` echoed by the facade. Informational for desktop. */
  readonly returnTo?: string;
  /**
   * Invoked with a cancel function as soon as the loopback is listening.
   * Calling it aborts the pending flow (rejects the code promise and
   * frees the port) — used so a second sign-in replaces the first.
   */
  readonly onCancelAvailable?: (cancel: () => void) => void;
}

interface StartResponse {
  readonly auth_url: string;
  readonly state: string;
  readonly expires_at: string;
}

interface CallbackHandoff {
  readonly user_id: string;
  readonly session_id: string;
  readonly bearer_token: string;
  readonly expires_at: string;
  readonly return_to: string | null;
  readonly requires_mfa: boolean;
}

export class GoogleLoginError extends Error {
  readonly stage: "start" | "redirect" | "handoff" | "mfa";

  constructor(stage: GoogleLoginError["stage"], message: string) {
    super(message);
    this.name = "GoogleLoginError";
    this.stage = stage;
  }
}

export async function runGoogleLogin(
  workspaceId: string,
  deps: GoogleLoginDeps,
): Promise<AuthSession> {
  const facadeBaseUrl = trimTrailingSlash(deps.facadeBaseUrl);
  const doFetch = deps.fetch ?? globalThis.fetch.bind(globalThis);
  const loopback = deps.loopback ?? awaitLoopbackCode;
  const clock = deps.clock ?? Date.now;

  const handle: LoopbackHandle = await loopback({
    callbackPath: CALLBACK_PATH,
    timeoutMs: deps.timeoutMs ?? DEFAULT_TIMEOUT_MS,
    randomPorts: {},
  });
  deps.onCancelAvailable?.(handle.close);
  try {
    // -- 1. ask the facade to build the Google auth URL --------------------
    const startUrl = new URL(`${facadeBaseUrl}/v1/auth/oidc/google/start`);
    startUrl.searchParams.set("redirect_uri", handle.redirectUri);
    startUrl.searchParams.set("format", "json");
    if (deps.returnTo !== undefined) {
      startUrl.searchParams.set("return_to", deps.returnTo);
    }
    const startResponse = await doFetch(startUrl.toString(), {
      method: "GET",
      headers: { accept: "application/json" },
    });
    if (!startResponse.ok) {
      throw new GoogleLoginError(
        "start",
        `google sign-in start failed: ${startResponse.status} ${await safeText(startResponse)}`,
      );
    }
    const start = (await startResponse.json()) as StartResponse;
    if (!start.auth_url || !start.state) {
      throw new GoogleLoginError(
        "start",
        "google sign-in start returned no auth_url/state",
      );
    }

    // -- 2. system browser round-trip --------------------------------------
    handle.armState(start.state);
    await deps.openExternal(start.auth_url);
    let received: { code: string; state: string };
    try {
      received = await handle.codePromise;
    } catch (err) {
      throw new GoogleLoginError(
        "redirect",
        err instanceof Error ? err.message : String(err),
      );
    }

    // -- 3. bearer handoff (JSON body — no cookie, no fragment) -------------
    const callbackUrl = new URL(`${facadeBaseUrl}/v1/auth/oidc/callback`);
    callbackUrl.searchParams.set("state", received.state);
    callbackUrl.searchParams.set("code", received.code);
    const callbackResponse = await doFetch(callbackUrl.toString(), {
      method: "GET",
      headers: { accept: "application/json" },
    });
    if (!callbackResponse.ok) {
      throw new GoogleLoginError(
        "handoff",
        handoffFailureMessage(
          callbackResponse.status,
          await safeText(callbackResponse),
        ),
      );
    }
    const handoff = (await callbackResponse.json()) as CallbackHandoff;
    if (handoff.requires_mfa) {
      // The minted session carries scope "mfa:pending"; the desktop app has
      // no MFA challenge surface yet, so storing it would only produce 403s.
      throw new GoogleLoginError(
        "mfa",
        "this account requires multi-factor authentication — sign in via the web app to complete MFA",
      );
    }

    // -- 4. best-effort display claims --------------------------------------
    const claims = await fetchProfileClaims(
      doFetch,
      facadeBaseUrl,
      handoff.bearer_token,
      handoff.user_id,
      workspaceId,
    );

    const expiresAtMs = Date.parse(handoff.expires_at);
    return {
      idToken: null,
      accessToken: handoff.bearer_token,
      refreshToken: null,
      expiresAt: Number.isNaN(expiresAtMs)
        ? clock() + DEFAULT_SESSION_TTL_MS
        : expiresAtMs,
      claims,
    };
  } finally {
    handle.close();
  }
}

function handoffFailureMessage(status: number, detail: string): string {
  const suffix = detail.length > 0 ? ` ${detail}` : "";
  if (status === 400) {
    return `google sign-in handoff rejected (state invalid, expired, or replayed): 400${suffix}`;
  }
  if (status === 401) {
    return `google sign-in not authorized: 401${suffix}`;
  }
  return `google sign-in handoff failed: ${status}${suffix}`;
}

function trimTrailingSlash(url: string): string {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}

async function safeText(response: Response): Promise<string> {
  try {
    return await response.text();
  } catch {
    return "";
  }
}
