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
export const SIWE_STATEMENT = "Sign in to Atlas";

/** EIP-4361 version field. Only "1" exists. */
export const SIWE_VERSION = "1";

/**
 * The EIP-4361 message layout (required fields only — no Expiration
 * Time / Request ID / Resources lines; nonce TTL is enforced
 * server-side from the nonce row, and `expired_message` is derived from
 * Issued At). `{placeholders}` are filled by `buildSiweMessage`.
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
  "Issued At: {issued_at}";

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
    .replace("{issued_at}", fields.issuedAt);
}
