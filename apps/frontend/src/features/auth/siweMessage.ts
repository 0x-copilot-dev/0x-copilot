/**
 * EIP-4361 ("Sign-In with Ethereum") message construction.
 *
 * The signed text is a *wire contract*: the backend re-parses it on
 * `POST /v1/auth/siwe/verify` and rejects any drift (whitespace, field
 * order, casing) — so the template lives in exactly one exported const
 * here and MUST stay byte-identical to the backend's template in
 * `services/backend/src/backend_app/identity/siwe.py`. If you change
 * either side, change both in the same PR and update the fixture in
 * `siweMessage.test.ts`.
 */

import { toEip55Address } from "../../utils/eip55";

/** Statement line — frozen by the SIWE wire contract. */
export const SIWE_STATEMENT = "Sign in to Copilot";

/** EIP-4361 version field. Only "1" exists. */
export const SIWE_VERSION = "1";

/**
 * The EIP-4361 message layout. `Expiration Time` is REQUIRED by the
 * backend parser (`SiweMessageInvalid: "Expiration Time is required"`);
 * clients set it to Issued At + 5 minutes, matching the server-side
 * nonce TTL. No Not Before / Request ID / Resources lines.
 * `{placeholders}` are filled by `buildSiweMessage`.
 *
 * Keep byte-identical to `backend_app/identity/siwe.py`.
 */
export const SIWE_MESSAGE_TEMPLATE =
  "{domain} wants you to sign in with your Ethereum account:\n" +
  "{address}\n" +
  "\n" +
  "{statement}\n" +
  "\n" +
  "URI: {uri}\n" +
  "Version: {version}\n" +
  "Chain ID: {chain_id}\n" +
  "Nonce: {nonce}\n" +
  "Issued At: {issued_at}\n" +
  "Expiration Time: {expiration_time}";

/** Message validity window — mirrors the server-side nonce TTL. */
export const SIWE_MESSAGE_TTL_MS = 5 * 60 * 1000;

export interface SiweMessageFields {
  /** Serving origin's host (`window.location.host`) — must match what
   * the backend expects or verify fails with `domain_mismatch`. */
  domain: string;
  /** Wallet address in any casing; rendered EIP-55 per the EIP-4361 ABNF. */
  address: string;
  /** Serving origin (`window.location.origin`). */
  uri: string;
  /** EIP-155 chain id (decimal). */
  chainId: number;
  /** Single-use nonce from `POST /v1/auth/siwe/nonce`. */
  nonce: string;
  /** ISO-8601 timestamp, e.g. `new Date().toISOString()`. */
  issuedAt: string;
  /** ISO-8601 expiry — the backend rejects messages without it. Use
   * `issuedAt + SIWE_MESSAGE_TTL_MS` (helper: `defaultExpirationTime`). */
  expirationTime: string;
}

/** Issued At + the server-mirrored TTL, as ISO-8601. */
export function defaultExpirationTime(issuedAt: string): string {
  return new Date(
    new Date(issuedAt).getTime() + SIWE_MESSAGE_TTL_MS,
  ).toISOString();
}

/** Fill the frozen template. The result is what `personal_sign` signs
 * and what `POST /v1/auth/siwe/verify` receives — verbatim. */
export function buildSiweMessage(fields: SiweMessageFields): string {
  if (!Number.isInteger(fields.chainId) || fields.chainId <= 0) {
    throw new Error(`invalid chain id: ${String(fields.chainId)}`);
  }
  return SIWE_MESSAGE_TEMPLATE.replace("{domain}", fields.domain)
    .replace("{address}", toEip55Address(fields.address))
    .replace("{statement}", SIWE_STATEMENT)
    .replace("{uri}", fields.uri)
    .replace("{version}", SIWE_VERSION)
    .replace("{chain_id}", String(fields.chainId))
    .replace("{nonce}", fields.nonce)
    .replace("{issued_at}", fields.issuedAt)
    .replace("{expiration_time}", fields.expirationTime);
}
