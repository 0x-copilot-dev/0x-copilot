/**
 * Pins the hand-rolled Keccak-256 to published vectors. If any of these
 * fail, EIP-55 checksumming (and therefore the SIWE message address
 * field) is wrong — do not weaken them.
 */

import { describe, expect, it } from "vitest";

import { keccak256Hex } from "./keccak256";

function ascii(text: string): Uint8Array {
  return new TextEncoder().encode(text);
}

describe("keccak256", () => {
  it("matches the empty-string vector", () => {
    expect(keccak256Hex(ascii(""))).toBe(
      "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
    );
  });

  it('matches the "abc" vector', () => {
    expect(keccak256Hex(ascii("abc"))).toBe(
      "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45",
    );
  });

  it("matches the quick-brown-fox vector", () => {
    expect(
      keccak256Hex(ascii("The quick brown fox jumps over the lazy dog")),
    ).toBe("4d741b6f1eb29cb2a9b9911c82f56fa8d73b04959d3d9d222895df6c0b28aa15");
  });
});
