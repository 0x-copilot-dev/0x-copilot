/**
 * EIP-55 checksum vectors straight from the EIP's test set, plus the
 * all-caps/all-lower canonical examples.
 */

import { describe, expect, it } from "vitest";

import { toEip55Address, toWireAddress } from "./eip55";

const VECTORS = [
  "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
  "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
  "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
  "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
];

describe("toEip55Address", () => {
  it.each(VECTORS)("checksums %s from lowercase input", (checksummed) => {
    expect(toEip55Address(checksummed.toLowerCase())).toBe(checksummed);
  });

  it.each(VECTORS)("checksums %s from uppercase input", (checksummed) => {
    expect(toEip55Address(`0x${checksummed.slice(2).toUpperCase()}`)).toBe(
      checksummed,
    );
  });

  it("rejects non-address inputs", () => {
    expect(() => toEip55Address("0x1234")).toThrow(/invalid ethereum address/);
    expect(() => toEip55Address("not-an-address")).toThrow(
      /invalid ethereum address/,
    );
    expect(() =>
      toEip55Address("0xZZ6916095ca1df60bb79ce92ce3ea74c37c5d359"),
    ).toThrow(/invalid ethereum address/);
  });
});

describe("toWireAddress", () => {
  it("lowercases and validates", () => {
    expect(toWireAddress("0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359")).toBe(
      "0xfb6916095ca1df60bb79ce92ce3ea74c37c5d359",
    );
    expect(() => toWireAddress("0x123")).toThrow(/invalid ethereum address/);
  });
});
