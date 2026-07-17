/**
 * EIP-55 mixed-case checksum encoding for Ethereum addresses.
 *
 * The SIWE contract stores addresses lowercase server-side; EIP-55 is a
 * client display concern — and the EIP-4361 message spec requires the
 * address field to be checksummed, so the sign-in message builder uses
 * this too.
 */

import { keccak256Hex } from "./keccak256";

const LOWER_HEX_ADDRESS_RE = /^[0-9a-f]{40}$/;

/**
 * Returns the EIP-55 checksummed form of a 0x-prefixed address.
 * Accepts any input casing; throws on anything that is not a 20-byte
 * hex address.
 */
export function toEip55Address(address: string): string {
  const bare = address.startsWith("0x") ? address.slice(2) : address;
  const lower = bare.toLowerCase();
  if (!LOWER_HEX_ADDRESS_RE.test(lower)) {
    throw new Error(`invalid ethereum address: ${address}`);
  }
  const hash = keccak256Hex(new TextEncoder().encode(lower));
  let out = "0x";
  for (let i = 0; i < 40; i += 1) {
    const char = lower[i];
    out +=
      char >= "a" && parseInt(hash[i], 16) >= 8 ? char.toUpperCase() : char;
  }
  return out;
}

/** Lowercase 0x-prefixed form — the wire/storage representation. */
export function toWireAddress(address: string): string {
  // Round-trip through the checksummer purely for shape validation.
  return toEip55Address(address).toLowerCase();
}
