import { privateKeyToAccount, type PrivateKeyAccount } from "viem/accounts";

import type { AuthSession } from "./oidc-client";
import { fetchProfileClaims } from "./profile-claims";

// "Use locally, no account" — a genuinely local sign-in for the packaged app.
//
// Instead of an external wallet (SIWE via the browser) or an IdP (Google/OIDC),
// the desktop mints its OWN per-install Ethereum key (generated once, kept in the
// OS keychain by SecretStorage) and drives the SAME SIWE self-signup ramp the
// wallet page uses — signing the EIP-4361 message itself, in-process:
//
//   1. POST {facade}/v1/auth/siwe/nonce  { address, chain_id }
//   2. build the EIP-4361 message (domain/uri = the facade origin, which the
//      supervisor pins as SIWE_ORIGIN) and sign it with the local key
//   3. POST {facade}/v1/auth/siwe/verify { message, signature }  → session
//   4. best-effort GET /v1/me/profile to fill renderer-facing display claims
//
// The result is a stable, private, no-account local identity — no browser, no
// wallet, no external service — reusing the production-safe SIWE backend. The
// bearer never leaves the main process; the caller persists it like every other
// sign-in mode.

// EIP-4361 statement — byte-identical to the SIWE wire contract in
// apps/frontend/src/features/auth/siweMessage.ts and
// services/backend/src/backend_app/identity/siwe.py. Change all three together.
const SIWE_STATEMENT = "Sign in to Copilot";
const SIWE_MESSAGE_TTL_MS = 5 * 60 * 1000;
const DEFAULT_SESSION_TTL_MS = 60 * 60 * 1000;
// Robinhood Chain (EIP-155 id 4663), the product's home chain and part of the
// backend's DEFAULT_ALLOWED_CHAIN_IDS (1 Ethereum · 8453 Base · 42161 Arbitrum
// One · 4663 Robinhood). The local key is a standard EVM (secp256k1) key, so its
// address is identical across every EVM chain — the identity is portable; the
// chain id only scopes the SIWE nonce, and we never transact on-chain.
const LOCAL_CHAIN_ID = 4663;

export interface LocalLoginDeps {
  readonly facadeBaseUrl: string;
  /** The per-install local key (hex, 0x-prefixed). Owned + persisted by the caller. */
  readonly privateKey: `0x${string}`;
  readonly fetch?: typeof fetch;
  readonly clock?: () => number;
}

export class LocalLoginError extends Error {
  readonly stage: "nonce" | "verify" | "mfa";

  constructor(stage: LocalLoginError["stage"], message: string) {
    super(message);
    this.name = "LocalLoginError";
    this.stage = stage;
  }
}

interface SiweVerifyHandoff {
  readonly user_id: string;
  readonly session_id: string;
  readonly bearer_token: string;
  readonly expires_at: string;
  readonly requires_mfa: boolean;
  readonly return_to: string | null;
}

function trimTrailingSlash(url: string): string {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}

function buildSiweMessage(fields: {
  domain: string;
  address: string;
  uri: string;
  chainId: number;
  nonce: string;
  issuedAt: string;
  expirationTime: string;
}): string {
  // Field order + blank lines are load-bearing (the backend re-parses strictly).
  return (
    `${fields.domain} wants you to sign in with your Ethereum account:\n` +
    `${fields.address}\n` +
    `\n` +
    `${SIWE_STATEMENT}\n` +
    `\n` +
    `URI: ${fields.uri}\n` +
    `Version: 1\n` +
    `Chain ID: ${fields.chainId}\n` +
    `Nonce: ${fields.nonce}\n` +
    `Issued At: ${fields.issuedAt}\n` +
    `Expiration Time: ${fields.expirationTime}`
  );
}

async function safeText(response: Response): Promise<string> {
  try {
    return await response.text();
  } catch {
    return "";
  }
}

export async function runLocalLogin(
  workspaceId: string,
  deps: LocalLoginDeps,
): Promise<AuthSession> {
  const facadeBaseUrl = trimTrailingSlash(deps.facadeBaseUrl);
  const doFetch = deps.fetch ?? globalThis.fetch.bind(globalThis);
  const clock = deps.clock ?? Date.now;

  const account: PrivateKeyAccount = privateKeyToAccount(deps.privateKey);
  const address = account.address; // EIP-55 checksummed by viem

  // The SIWE domain/uri MUST equal the origin the backend expects
  // (SIWE_ORIGIN, pinned by the supervisor to the facade origin).
  const origin = new URL(facadeBaseUrl);
  const domain = origin.host;
  const uri = origin.origin;

  // -- 1. nonce -------------------------------------------------------------
  const nonceResponse = await doFetch(`${facadeBaseUrl}/v1/auth/siwe/nonce`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ address, chain_id: LOCAL_CHAIN_ID }),
  });
  if (!nonceResponse.ok) {
    throw new LocalLoginError(
      "nonce",
      `local sign-in nonce failed: ${nonceResponse.status} ${await safeText(nonceResponse)}`,
    );
  }
  const nonceBody = (await nonceResponse.json()) as { nonce?: string };
  if (!nonceBody.nonce) {
    throw new LocalLoginError("nonce", "local sign-in nonce returned no nonce");
  }

  // -- 2. build + sign the EIP-4361 message ---------------------------------
  const issuedAt = new Date(clock()).toISOString();
  const expirationTime = new Date(clock() + SIWE_MESSAGE_TTL_MS).toISOString();
  const message = buildSiweMessage({
    domain,
    address,
    uri,
    chainId: LOCAL_CHAIN_ID,
    nonce: nonceBody.nonce,
    issuedAt,
    expirationTime,
  });
  const signature = await account.signMessage({ message });

  // -- 3. verify ------------------------------------------------------------
  const verifyResponse = await doFetch(`${facadeBaseUrl}/v1/auth/siwe/verify`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message, signature }),
  });
  if (!verifyResponse.ok) {
    throw new LocalLoginError(
      "verify",
      `local sign-in verify failed: ${verifyResponse.status} ${await safeText(verifyResponse)}`,
    );
  }
  const handoff = (await verifyResponse.json()) as SiweVerifyHandoff;
  if (handoff.requires_mfa) {
    throw new LocalLoginError(
      "mfa",
      "this local identity requires multi-factor authentication — unexpected for a local account",
    );
  }

  // -- 4. best-effort display claims ----------------------------------------
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
}
