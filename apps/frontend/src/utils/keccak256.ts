/**
 * Minimal Keccak-256 (the pre-NIST-padding variant Ethereum uses — pad
 * byte 0x01, NOT SHA-3's 0x06).
 *
 * Why hand-rolled: the wallet sign-in flow (SIWE, EIP-4361) needs exactly
 * one hash — EIP-55 address checksumming — and the repo policy for that
 * feature is "no wagmi/viem". Pulling a whole EVM toolkit in for 40-char
 * inputs would be the tail wagging the dog; a BigInt-lane Keccak-f[1600]
 * is ~80 lines and is pinned to the official test vectors in
 * `keccak256.test.ts`. Performance is irrelevant at this call rate —
 * do NOT reach for this module for bulk hashing.
 */

const MASK64 = (1n << 64n) - 1n;

/** Round constants for Keccak-f[1600] (24 rounds). */
const ROUND_CONSTANTS: readonly bigint[] = [
  0x0000000000000001n,
  0x0000000000008082n,
  0x800000000000808an,
  0x8000000080008000n,
  0x000000000000808bn,
  0x0000000080000001n,
  0x8000000080008081n,
  0x8000000000008009n,
  0x000000000000008an,
  0x0000000000000088n,
  0x0000000080008009n,
  0x000000008000000an,
  0x000000008000808bn,
  0x800000000000008bn,
  0x8000000000008089n,
  0x8000000000008003n,
  0x8000000000008002n,
  0x8000000000000080n,
  0x000000000000800an,
  0x800000008000000an,
  0x8000000080008081n,
  0x8000000000008080n,
  0x0000000080000001n,
  0x8000000080008008n,
];

/** Rho rotation offsets, indexed by lane position `x + 5y`. */
const RHO_OFFSETS: readonly number[] = [
  0, 1, 62, 28, 27, 36, 44, 6, 55, 20, 3, 10, 43, 25, 39, 41, 45, 15, 21, 8, 18,
  2, 61, 56, 14,
];

function rotl64(value: bigint, shift: number): bigint {
  if (shift === 0) return value;
  const s = BigInt(shift);
  return ((value << s) | (value >> (64n - s))) & MASK64;
}

function keccakF1600(state: bigint[]): void {
  for (let round = 0; round < 24; round += 1) {
    // θ
    const c = new Array<bigint>(5);
    for (let x = 0; x < 5; x += 1) {
      c[x] =
        state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20];
    }
    for (let x = 0; x < 5; x += 1) {
      const d = c[(x + 4) % 5] ^ rotl64(c[(x + 1) % 5], 1);
      for (let y = 0; y < 25; y += 5) {
        state[x + y] ^= d;
      }
    }
    // ρ + π
    const b = new Array<bigint>(25);
    for (let x = 0; x < 5; x += 1) {
      for (let y = 0; y < 5; y += 1) {
        const source = x + 5 * y;
        const dest = y + 5 * ((2 * x + 3 * y) % 5);
        b[dest] = rotl64(state[source], RHO_OFFSETS[source]);
      }
    }
    // χ
    for (let x = 0; x < 5; x += 1) {
      for (let y = 0; y < 25; y += 5) {
        state[x + y] =
          b[x + y] ^ (~b[((x + 1) % 5) + y] & MASK64 & b[((x + 2) % 5) + y]);
      }
    }
    // ι
    state[0] ^= ROUND_CONSTANTS[round];
  }
}

/** Keccak-256 digest (32 bytes) of `input`. */
export function keccak256(input: Uint8Array): Uint8Array {
  const rate = 136; // 1088-bit rate for 256-bit output
  const padded = new Uint8Array(Math.ceil((input.length + 1) / rate) * rate);
  padded.set(input);
  padded[input.length] = 0x01; // Keccak multi-rate padding (not SHA-3 0x06)
  padded[padded.length - 1] |= 0x80;

  const state = new Array<bigint>(25).fill(0n);
  for (let offset = 0; offset < padded.length; offset += rate) {
    for (let lane = 0; lane < rate / 8; lane += 1) {
      let value = 0n;
      for (let byte = 7; byte >= 0; byte -= 1) {
        value = (value << 8n) | BigInt(padded[offset + lane * 8 + byte]);
      }
      state[lane] ^= value;
    }
    keccakF1600(state);
  }

  const out = new Uint8Array(32);
  for (let lane = 0; lane < 4; lane += 1) {
    let value = state[lane];
    for (let byte = 0; byte < 8; byte += 1) {
      out[lane * 8 + byte] = Number(value & 0xffn);
      value >>= 8n;
    }
  }
  return out;
}

/** Keccak-256 digest of `input`, as a lowercase hex string (no 0x). */
export function keccak256Hex(input: Uint8Array): string {
  let hex = "";
  for (const byte of keccak256(input)) {
    hex += byte.toString(16).padStart(2, "0");
  }
  return hex;
}
