/**
 * Shared SIWE (EIP-4361) wallet-proof primitives — the raw EIP-1193
 * `request` calls that both the sign-in ramp (`WalletSignIn`) and the
 * authenticated wallet-LINK ramp (`WalletLinkFlow`, account-linking
 * PRD FR-L1) drive. Extracted verbatim from `WalletSignIn` so the two
 * flows share one copy of the personal_sign gotcha + the user-rejection
 * detection rather than duplicating them.
 *
 * No wagmi/viem: the wallet is driven through raw EIP-1193 `request`.
 */

import type { SiweNonceResponse } from "@0x-copilot/api-types";

import { requestSiweNonce } from "../../api/siweApi";
import { toWireAddress } from "../../utils/eip55";
import type { Eip1193Provider } from "./eip6963";
import { buildSiweMessage, defaultExpirationTime } from "./siweMessage";

/** `eth_requestAccounts` → `eth_chainId`. Throws on a malformed wallet reply. */
export async function connectWallet(
  provider: Eip1193Provider,
): Promise<{ address: string; chainId: number }> {
  const accounts = await provider.request({ method: "eth_requestAccounts" });
  if (
    !Array.isArray(accounts) ||
    accounts.length === 0 ||
    typeof accounts[0] !== "string"
  ) {
    throw new Error("wallet returned no accounts");
  }
  const address = accounts[0];

  const chainHex = await provider.request({ method: "eth_chainId" });
  const chainId =
    typeof chainHex === "string" ? Number.parseInt(chainHex, 16) : Number.NaN;
  if (!Number.isInteger(chainId) || chainId <= 0) {
    throw new Error("wallet returned an invalid chain id");
  }
  return { address, chainId };
}

export async function personalSignSiwe(
  provider: Eip1193Provider,
  message: string,
  address: string,
): Promise<string> {
  // personal_sign takes the hex-encoded UTF-8 message first, then the
  // signing address (the reverse of eth_sign — a classic wallet gotcha).
  const signature = await provider.request({
    method: "personal_sign",
    params: [hexEncodeUtf8(message), address],
  });
  if (typeof signature !== "string" || !signature.startsWith("0x")) {
    throw new Error("wallet returned an invalid signature");
  }
  return signature;
}

export function hexEncodeUtf8(text: string): string {
  let hex = "0x";
  for (const byte of new TextEncoder().encode(text)) {
    hex += byte.toString(16).padStart(2, "0");
  }
  return hex;
}

/** EIP-1193 ProviderRpcError code 4001 — "User Rejected Request". */
export function isWalletUserRejection(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    "code" in err &&
    (err as { code: unknown }).code === 4001
  );
}

/**
 * Collect a full SIWE proof for a chosen provider: connect → mint a
 * server nonce → build the frozen EIP-4361 message → `personal_sign`.
 * Returns the `{ message, signature }` pair either `/v1/auth/siwe/verify`
 * (sign-in) or `POST /v1/me/identities/wallet` (link) consumes. Every
 * call mints a FRESH single-use nonce — so a merge-confirm re-submit must
 * call this again rather than replaying the first proof.
 */
export async function collectWalletSiweProof(
  provider: Eip1193Provider,
  hooks?: {
    onConnecting?: () => void;
    onSigning?: () => void;
    /** Injectable for tests; defaults to the real facade nonce call. */
    requestNonce?: (
      address: string,
      chainId: number,
    ) => Promise<SiweNonceResponse>;
    /** Injectable for tests; defaults to `new Date().toISOString()`. */
    now?: () => string;
  },
): Promise<{
  message: string;
  signature: string;
  address: string;
  chainId: number;
}> {
  hooks?.onConnecting?.();
  const { address, chainId } = await connectWallet(provider);

  const nonce = hooks?.requestNonce
    ? await hooks.requestNonce(toWireAddress(address), chainId)
    : await requestSiweNonce({
        address: toWireAddress(address),
        chain_id: chainId,
      });

  const issuedAt = hooks?.now ? hooks.now() : new Date().toISOString();
  const message = buildSiweMessage({
    domain: window.location.host,
    uri: window.location.origin,
    address,
    chainId,
    nonce: nonce.nonce,
    issuedAt,
    expirationTime: defaultExpirationTime(issuedAt),
  });

  hooks?.onSigning?.();
  const signature = await personalSignSiwe(provider, message, address);
  return { message, signature, address, chainId };
}
