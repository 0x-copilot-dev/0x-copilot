import {
  awaitLoopbackWalletProof,
  type LoopbackWalletProof,
  type LoopbackWalletProofHandle,
} from "./loopback-server";

// Authenticated wallet LINK (account-linking PRD FR-L1/M1) — the sibling of
// wallet-login.ts. The wallet page runs in LINK mode: it drives the same
// EIP-6963 pick + SIWE sign, but delivers the raw `{ message, signature }`
// PROOF to the loopback instead of minting a session. The desktop main
// process then POSTs the proof to `/v1/me/identities/wallet` with the
// caller's own bearer, so the wallet binds to the CURRENT account:
//
//   1. mint a random `state`, bind an ephemeral loopback (armed with it)
//   2. open {facade}/wallet.html?mode=link&handoff=<loopback?state=…>
//   3. the page signs and redirects to the loopback with ?message&signature
//   4. POST {facade}/v1/me/identities/wallet (Bearer <caller>)
//      { message, signature, confirm_merge } → the link result
//
// A wallet owned by another account comes back 409 `merge_required`; the
// renderer re-invokes with `confirmMerge: true`, which re-runs this whole
// flow (a FRESH signature — the SIWE nonce is single-use) and merges.

import { randomBytes } from "node:crypto";

const DEFAULT_TIMEOUT_MS = 5 * 60 * 1000;
const CALLBACK_PATH = "/wallet/link/cb";
const WALLET_PAGE_PATH = "/wallet.html";

export type WalletLinkStatus =
  | "linked"
  | "already_linked"
  | "merged"
  | "merge_required"
  | "error";

export interface WalletLinkResult {
  readonly status: WalletLinkStatus;
  readonly message: string | null;
}

export interface WalletLinkDeps {
  readonly facadeBaseUrl: string;
  /** Caller's bearer for the authenticated link POST (never leaves main). */
  readonly bearer: string;
  readonly confirmMerge: boolean;
  readonly openExternal: (url: string) => Promise<void>;
  readonly fetch?: typeof fetch;
  readonly loopback?: typeof awaitLoopbackWalletProof;
  readonly timeoutMs?: number;
  readonly generateState?: () => string;
  readonly onCancelAvailable?: (cancel: () => void) => void;
}

export class WalletLinkError extends Error {
  readonly stage: "open" | "redirect";

  constructor(stage: WalletLinkError["stage"], message: string) {
    super(message);
    this.name = "WalletLinkError";
    this.stage = stage;
  }
}

function defaultGenerateState(): string {
  return randomBytes(16).toString("hex");
}

export async function runWalletLink(
  deps: WalletLinkDeps,
): Promise<WalletLinkResult> {
  const facadeBaseUrl = trimTrailingSlash(deps.facadeBaseUrl);
  const doFetch = deps.fetch ?? globalThis.fetch.bind(globalThis);
  const loopback = deps.loopback ?? awaitLoopbackWalletProof;
  const state = (deps.generateState ?? defaultGenerateState)();

  const handle: LoopbackWalletProofHandle = await loopback({
    callbackPath: CALLBACK_PATH,
    timeoutMs: deps.timeoutMs ?? DEFAULT_TIMEOUT_MS,
    randomPorts: {},
    expectedState: state,
  });
  deps.onCancelAvailable?.(handle.close);
  try {
    // -- 1. open the wallet page in LINK mode ------------------------------
    const handoffTarget = new URL(handle.redirectUri);
    handoffTarget.searchParams.set("state", state);
    const pageUrl = new URL(`${facadeBaseUrl}${WALLET_PAGE_PATH}`);
    pageUrl.searchParams.set("mode", "link");
    pageUrl.searchParams.set("handoff", handoffTarget.toString());
    try {
      await deps.openExternal(pageUrl.toString());
    } catch (err) {
      throw new WalletLinkError(
        "open",
        `could not open the system browser for wallet linking: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    }

    // -- 2. wait for the signed proof --------------------------------------
    let proof: LoopbackWalletProof;
    try {
      proof = await handle.proofPromise;
    } catch (err) {
      throw new WalletLinkError(
        "redirect",
        err instanceof Error ? err.message : String(err),
      );
    }

    // -- 3. POST the proof with the caller's bearer ------------------------
    const response = await doFetch(`${facadeBaseUrl}/v1/me/identities/wallet`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "application/json",
        authorization: `Bearer ${deps.bearer}`,
      },
      body: JSON.stringify({
        message: proof.message,
        signature: proof.signature,
        confirm_merge: deps.confirmMerge,
      }),
    });
    return await parseWalletLinkResponse(response);
  } finally {
    handle.close();
  }
}

/** Map the `/v1/me/identities/wallet` response to a renderer-safe outcome. */
export async function parseWalletLinkResponse(
  response: Response,
): Promise<WalletLinkResult> {
  if (response.ok) {
    const body = (await safeJson(response)) as { status?: unknown };
    const status =
      body.status === "linked" ||
      body.status === "already_linked" ||
      body.status === "merged"
        ? body.status
        : "linked";
    return { status, message: null };
  }
  const detail = await extractDetail(response);
  if (response.status === 409 && detail?.code === "merge_required") {
    return { status: "merge_required", message: detail.safe_message ?? null };
  }
  return {
    status: "error",
    message: detail?.safe_message ?? `wallet link failed (${response.status})`,
  };
}

async function extractDetail(
  response: Response,
): Promise<{ code?: string; safe_message?: string } | null> {
  const body = (await safeJson(response)) as { detail?: unknown } | null;
  if (body && typeof body.detail === "object" && body.detail !== null) {
    return body.detail as { code?: string; safe_message?: string };
  }
  return null;
}

async function safeJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function trimTrailingSlash(url: string): string {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}
