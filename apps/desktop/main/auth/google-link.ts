import { awaitLoopbackCode, type LoopbackHandle } from "./loopback-server";

// Authenticated Google LINK (account-linking PRD FR-L2) — the sibling of
// google-login.ts, but it attaches a Google identity to the CALLER's
// existing account instead of minting a new session:
//
//   1. bind an ephemeral loopback server (random port, conflict retry)
//   2. POST {facade}/v1/me/identities/google/link/start (Bearer <caller>)
//      { redirect_uri: <loopback>, return_to } → { auth_url, state }
//      (the link binding is written server-side onto the state row from the
//       verified session — never in the browser round-trip)
//   3. arm the loopback with `state`, open `auth_url` in the system browser
//   4. Google redirects the browser to the loopback with ?state&code
//   5. GET {facade}/v1/auth/oidc/callback?state&code — the facade RESOLVES the
//      link server-side and 302-redirects to the in-app landing with the
//      outcome in the query string. We DON'T follow the redirect; we read
//      `link_status` (+ provider / email_upgraded) straight off the Location.
//
// The bearer is attached to the start POST in main and never leaves it; the
// callback is public (the link intent lives on the consumed state row).

const DEFAULT_TIMEOUT_MS = 5 * 60 * 1000;
const CALLBACK_PATH = "/oidc/link/cb";

export type GoogleLinkStatus =
  | "linked"
  | "already_linked"
  | "merge_required"
  | "error";

export interface GoogleLinkResult {
  readonly status: GoogleLinkStatus;
  readonly provider: string | null;
  readonly emailUpgraded: boolean;
  readonly message: string | null;
}

export interface GoogleLinkDeps {
  readonly facadeBaseUrl: string;
  /** Caller's bearer for the authenticated start POST (never leaves main). */
  readonly bearer: string;
  readonly openExternal: (url: string) => Promise<void>;
  readonly fetch?: typeof fetch;
  readonly loopback?: typeof awaitLoopbackCode;
  readonly timeoutMs?: number;
  readonly returnTo?: string;
  readonly onCancelAvailable?: (cancel: () => void) => void;
}

interface StartResponse {
  readonly auth_url: string;
  readonly state: string;
}

export class GoogleLinkError extends Error {
  readonly stage: "start" | "redirect" | "callback";

  constructor(stage: GoogleLinkError["stage"], message: string) {
    super(message);
    this.name = "GoogleLinkError";
    this.stage = stage;
  }
}

export async function runGoogleLink(
  deps: GoogleLinkDeps,
): Promise<GoogleLinkResult> {
  const facadeBaseUrl = trimTrailingSlash(deps.facadeBaseUrl);
  const doFetch = deps.fetch ?? globalThis.fetch.bind(globalThis);
  const loopback = deps.loopback ?? awaitLoopbackCode;

  const handle: LoopbackHandle = await loopback({
    callbackPath: CALLBACK_PATH,
    timeoutMs: deps.timeoutMs ?? DEFAULT_TIMEOUT_MS,
    randomPorts: {},
  });
  deps.onCancelAvailable?.(handle.close);
  try {
    // -- 1. authenticated start: bind the link to the caller server-side ----
    const startResponse = await doFetch(
      `${facadeBaseUrl}/v1/me/identities/google/link/start`,
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          accept: "application/json",
          authorization: `Bearer ${deps.bearer}`,
        },
        body: JSON.stringify({
          redirect_uri: handle.redirectUri,
          return_to: deps.returnTo ?? null,
        }),
      },
    );
    if (!startResponse.ok) {
      throw new GoogleLinkError(
        "start",
        `google link start failed: ${startResponse.status} ${await safeText(startResponse)}`,
      );
    }
    const start = (await startResponse.json()) as StartResponse;
    if (!start.auth_url || !start.state) {
      throw new GoogleLinkError(
        "start",
        "google link start returned no auth_url/state",
      );
    }

    // -- 2. system browser round-trip ---------------------------------------
    handle.armState(start.state);
    await deps.openExternal(start.auth_url);
    let received: { code: string; state: string };
    try {
      received = await handle.codePromise;
    } catch (err) {
      throw new GoogleLinkError(
        "redirect",
        err instanceof Error ? err.message : String(err),
      );
    }

    // -- 3. complete on the public callback; read the link outcome off the
    //       302 Location (don't follow it — it points at the SPA landing) ----
    const callbackUrl = new URL(`${facadeBaseUrl}/v1/auth/oidc/callback`);
    callbackUrl.searchParams.set("state", received.state);
    callbackUrl.searchParams.set("code", received.code);
    const callbackResponse = await doFetch(callbackUrl.toString(), {
      method: "GET",
      headers: { accept: "application/json" },
      redirect: "manual",
    });
    return parseLinkOutcome(callbackResponse);
  } finally {
    handle.close();
  }
}

/** Read the link outcome from the facade's callback response. */
export async function parseLinkOutcome(
  response: Response,
): Promise<GoogleLinkResult> {
  // Success / conflict both come back as a 3xx redirect to the landing with
  // the outcome in the Location query string.
  if (response.status >= 300 && response.status < 400) {
    const location = response.headers.get("location") ?? "";
    const query = location.includes("?") ? location.split("?")[1] : "";
    const params = new URLSearchParams(query);
    const raw = params.get("link_status");
    const status: GoogleLinkStatus =
      raw === "linked" || raw === "already_linked" || raw === "merge_required"
        ? raw
        : "error";
    return {
      status,
      provider: params.get("provider"),
      emailUpgraded: params.get("email_upgraded") === "true",
      message: null,
    };
  }
  // Any non-redirect is an error (a sign-in JSON handoff would mean the
  // state row was never link-bound — treat as a failed link).
  return {
    status: "error",
    provider: null,
    emailUpgraded: false,
    message: `google link did not complete (${response.status})`,
  };
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
