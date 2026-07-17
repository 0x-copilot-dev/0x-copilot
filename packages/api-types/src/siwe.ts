/**
 * SIWE (EIP-4361) wallet sign-in wire shapes — `/v1/auth/siwe/*`.
 *
 * Mirrors the backend contracts served by `backend_app/identity/siwe.py`
 * through the facade. The flow is two round-trips:
 *
 *   1. `POST /v1/auth/siwe/nonce`  — bind a single-use nonce (TTL ≤ 10 min)
 *      to an address + chain. 422 on a malformed address; 400
 *      `{"detail":"chain_not_allowed"}` when the chain id is not in the
 *      deployment allowlist (`SIWE_ALLOWED_CHAIN_IDS`, default
 *      Ethereum / Base / Arbitrum One / Robinhood Chain).
 *   2. `POST /v1/auth/siwe/verify` — full EIP-4361 message text + wallet
 *      signature. On success the response is the same session-establishing
 *      handoff shape the OIDC callback returns (backend
 *      `OidcCallbackResult`), so the frontend adopts the session through
 *      the exact code path SSO uses.
 *
 * Addresses are stored lowercase server-side (unique per wallet);
 * display uses EIP-55 checksumming client-side.
 */

export interface SiweNonceRequest {
  /** 0x-prefixed 20-byte hex address. Sent lowercase (the server stores
   * lowercase; EIP-55 is a display concern). */
  address: string;
  /** EIP-155 chain id the wallet is currently on (decimal integer). */
  chain_id: number;
}

export interface SiweNonceResponse {
  /** Single-use nonce to embed in the EIP-4361 message. */
  nonce: string;
  /** ISO timestamp after which the nonce is rejected (`nonce_expired`). */
  expires_at: string;
}

export interface SiweVerifyRequest {
  /** The full EIP-4361 message text that was signed — byte-identical to
   * what `personal_sign` received. The server re-parses and validates it. */
  message: string;
  /** 0x-prefixed 65-byte secp256k1 signature from `personal_sign`. */
  signature: string;
}

/**
 * 400-level `detail` codes `POST /v1/auth/siwe/verify` returns. The 403
 * `self_signup_disabled` code arrives when the wallet is unknown and the
 * deployment profile forbids self-provisioning.
 */
export type SiweVerifyErrorDetail =
  | "nonce_invalid"
  | "nonce_expired"
  | "signature_invalid"
  | "domain_mismatch"
  | "chain_not_allowed"
  | "expired_message"
  | "self_signup_disabled";

/**
 * Session handoff returned by `POST /v1/auth/siwe/verify` — identical
 * to the shape `GET /v1/auth/oidc/callback` returns (backend
 * `OidcCallbackResult`): the SPA sets the bearer and refreshes, exactly
 * like the magic-link / workspace-pick completion paths.
 */
export interface SiweSessionResponse {
  user_id: string;
  session_id: string;
  bearer_token: string;
  /** ISO timestamp the minted session expires. */
  expires_at: string;
  return_to: string | null;
  /** When true the session carries the `mfa:pending` scope and the
   * frontend must route to the MFA prompt before anything else. */
  requires_mfa: boolean;
}
