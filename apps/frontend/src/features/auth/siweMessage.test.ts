/**
 * Message-template exactness. The fixture below is the frozen wire
 * layout the backend re-parses on /v1/auth/siwe/verify
 * (`backend_app/identity/siwe.py`). If this test needs editing, the
 * backend template changed — update both sides in the same PR.
 */

import { describe, expect, it } from "vitest";

import {
  buildSiweMessage,
  SIWE_MESSAGE_TEMPLATE,
  SIWE_STATEMENT,
  SIWE_VERSION,
} from "./siweMessage";

describe("buildSiweMessage", () => {
  it("produces the exact frozen EIP-4361 layout", () => {
    const message = buildSiweMessage({
      domain: "app.atlas.dev",
      // Lowercase in → EIP-55 out, per the EIP-4361 ABNF.
      address: "0xfb6916095ca1df60bb79ce92ce3ea74c37c5d359",
      uri: "https://app.atlas.dev",
      chainId: 8453,
      nonce: "k9GhL2mPqRstUv",
      issuedAt: "2026-07-17T10:30:00.000Z",
    });
    expect(message).toBe(
      "app.atlas.dev wants you to sign in with your Ethereum account:\n" +
        "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359\n" +
        "\n" +
        "Sign in to Atlas\n" +
        "\n" +
        "URI: https://app.atlas.dev\n" +
        "Version: 1\n" +
        "Chain ID: 8453\n" +
        "Nonce: k9GhL2mPqRstUv\n" +
        "Issued At: 2026-07-17T10:30:00.000Z",
    );
  });

  it("keeps the frozen statement and version", () => {
    expect(SIWE_STATEMENT).toBe("Sign in to Atlas");
    expect(SIWE_VERSION).toBe("1");
  });

  it("keeps every placeholder in the exported template", () => {
    for (const placeholder of [
      "{domain}",
      "{address}",
      "{statement}",
      "{uri}",
      "{version}",
      "{chain_id}",
      "{nonce}",
      "{issued_at}",
    ]) {
      expect(SIWE_MESSAGE_TEMPLATE).toContain(placeholder);
    }
  });

  it("rejects a non-positive or fractional chain id", () => {
    const fields = {
      domain: "app.atlas.dev",
      address: "0xfb6916095ca1df60bb79ce92ce3ea74c37c5d359",
      uri: "https://app.atlas.dev",
      nonce: "n",
      issuedAt: "2026-07-17T10:30:00.000Z",
    };
    expect(() => buildSiweMessage({ ...fields, chainId: 0 })).toThrow(
      /invalid chain id/,
    );
    expect(() => buildSiweMessage({ ...fields, chainId: 1.5 })).toThrow(
      /invalid chain id/,
    );
    expect(() => buildSiweMessage({ ...fields, chainId: Number.NaN })).toThrow(
      /invalid chain id/,
    );
  });
});
