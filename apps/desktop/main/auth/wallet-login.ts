import { randomBytes } from "node:crypto";

import type { AuthSession } from "./oidc-client";
import {
  awaitLoopbackHandoff,
  type LoopbackHandoff,
  type LoopbackHandoffHandle,
} from "./loopback-server";
import { fetchProfileClaims } from "./profile-claims";

// "Connect wallet" — Sign-In-With-Ethereum via the standalone wallet page
// (branch feat/siwe-frontend) + SIWE facade API (branch feat/siwe-backend).
// Sibling of google-login.ts, with one structural difference: there is no
// /start or /callback facade hop from the desktop. The wallet page runs
// the whole nonce → personal_sign → verify ramp itself and delivers the
// minted session straight to the loopback:
//
//   1. mint a random `state`, bind an ephemeral loopback server
//      (random port, conflict retry) armed with that state
//   2. open the system browser at
//        {facade}/wallet.html?handoff=http://127.0.0.1:<port>/wallet/cb?state=<state>
//      (the page refuses non-loopback handoff targets, and preserves the
//      query we embed — the state must round-trip)
//   3. the page drives the wallet (EIP-6963 pick, eth_requestAccounts,
//      personal_sign) and POSTs /v1/auth/siwe/nonce + /verify itself
//   4. on success it redirects the browser to the loopback with the
//      bearer handoff in the query string — same field names as the OIDC
//      callback handoff (user_id, session_id, bearer_token, expires_at,
//      requires_mfa, return_to)
//   5. best-effort GET /v1/me/profile with the new bearer to fill the
//      renderer-facing display claims (the handoff carries ids only)
//
// The bearer never leaves the main process; callers persist the returned
// AuthSession via SecretStorage exactly like the other sign-in modes.

const DEFAULT_TIMEOUT_MS = 5 * 60 * 1000;
const CALLBACK_PATH = "/wallet/cb";
const DEFAULT_SESSION_TTL_MS = 60 * 60 * 1000;
const WALLET_PAGE_PATH = "/wallet.html";

export interface WalletLoginDeps {
  readonly facadeBaseUrl: string;
  readonly openExternal: (url: string) => Promise<void>;
  readonly fetch?: typeof fetch;
  readonly loopback?: typeof awaitLoopbackHandoff;
  readonly clock?: () => number;
  readonly timeoutMs?: number;
  /** State minting, injectable for tests. Default: 16 random bytes hex. */
  readonly generateState?: () => string;
  /**
   * Invoked with a cancel function as soon as the loopback is listening.
   * Calling it aborts the pending flow (rejects the handoff promise and
   * frees the port) — used so a second sign-in replaces the first.
   */
  readonly onCancelAvailable?: (cancel: () => void) => void;
}

export class WalletLoginError extends Error {
  readonly stage: "open" | "redirect" | "mfa";

  constructor(stage: WalletLoginError["stage"], message: string) {
    super(message);
    this.name = "WalletLoginError";
    this.stage = stage;
  }
}

function defaultGenerateState(): string {
  return randomBytes(16).toString("hex");
}

export async function runWalletLogin(
  workspaceId: string,
  deps: WalletLoginDeps,
): Promise<AuthSession> {
  const facadeBaseUrl = trimTrailingSlash(deps.facadeBaseUrl);
  const doFetch = deps.fetch ?? globalThis.fetch.bind(globalThis);
  const loopback = deps.loopback ?? awaitLoopbackHandoff;
  const clock = deps.clock ?? Date.now;
  const state = (deps.generateState ?? defaultGenerateState)();

  // The state is known before the browser opens (WE mint it), so the
  // loopback is armed up-front — no deferred armState step here.
  const handle: LoopbackHandoffHandle = await loopback({
    callbackPath: CALLBACK_PATH,
    timeoutMs: deps.timeoutMs ?? DEFAULT_TIMEOUT_MS,
    randomPorts: {},
    expectedState: state,
  });
  deps.onCancelAvailable?.(handle.close);
  try {
    // -- 1. open the facade-served wallet page in the system browser -------
    // The wallet page appends the session fields to whatever query the
    // handoff target already carries, so ?state= survives the round-trip.
    const handoffTarget = new URL(handle.redirectUri);
    handoffTarget.searchParams.set("state", state);
    const pageUrl = new URL(`${facadeBaseUrl}${WALLET_PAGE_PATH}`);
    pageUrl.searchParams.set("handoff", handoffTarget.toString());
    try {
      await deps.openExternal(pageUrl.toString());
    } catch (err) {
      throw new WalletLoginError(
        "open",
        `could not open the system browser for wallet sign-in: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    }

    // -- 2. wait for the page to deliver the bearer handoff ----------------
    let handoff: LoopbackHandoff;
    try {
      handoff = await handle.handoffPromise;
    } catch (err) {
      throw new WalletLoginError(
        "redirect",
        err instanceof Error ? err.message : String(err),
      );
    }
    if (handoff.requiresMfa) {
      // The minted session carries scope "mfa:pending"; the desktop app has
      // no MFA challenge surface yet, so storing it would only produce 403s.
      throw new WalletLoginError(
        "mfa",
        "this account requires multi-factor authentication — sign in via the web app to complete MFA",
      );
    }

    // -- 3. best-effort display claims --------------------------------------
    const claims = await fetchProfileClaims(
      doFetch,
      facadeBaseUrl,
      handoff.bearerToken,
      handoff.userId,
      workspaceId,
    );

    const expiresAtMs = Date.parse(handoff.expiresAt);
    return {
      idToken: null,
      accessToken: handoff.bearerToken,
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

function trimTrailingSlash(url: string): string {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}
