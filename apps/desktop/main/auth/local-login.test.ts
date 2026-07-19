import { describe, expect, it, vi } from "vitest";
import { privateKeyToAccount } from "viem/accounts";

import { LocalLoginError, runLocalLogin } from "./local-login";

const FACADE = "http://127.0.0.1:54321";
// Deterministic well-known test key (hardhat account #0).
const PK =
  "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80" as const;
const ADDRESS = privateKeyToAccount(PK).address;

const HANDOFF = {
  user_id: "usr_local_1",
  session_id: "sess_1",
  bearer_token: "bearer-local-xyz",
  expires_at: "2999-01-01T00:00:00Z",
  requires_mfa: false,
  return_to: null,
};
const PROFILE = { display_name: "Local User", email: null };

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

type Handler = (
  url: string,
  init?: RequestInit,
) => Response | Promise<Response>;

function routedFetch(routes: Record<string, Handler>): typeof fetch {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : String(input);
    for (const [path, handler] of Object.entries(routes)) {
      if (url.includes(path)) return handler(url, init);
    }
    return new Response("not found", { status: 404 });
  }) as unknown as typeof fetch;
}

describe("runLocalLogin", () => {
  it("mints a session via the local-key SIWE ramp with a facade-origin message", async () => {
    let verifyPayload: { message?: string; signature?: string } = {};
    const fetchMock = routedFetch({
      "/v1/auth/siwe/nonce": () => jsonResponse({ nonce: "abcd1234efgh5678" }),
      "/v1/auth/siwe/verify": (_url, init) => {
        verifyPayload = JSON.parse(String(init?.body));
        return jsonResponse(HANDOFF);
      },
      "/v1/me/profile": () => jsonResponse(PROFILE),
    });

    const session = await runLocalLogin("org_acme", {
      facadeBaseUrl: FACADE,
      privateKey: PK,
      fetch: fetchMock,
    });

    expect(session.accessToken).toBe("bearer-local-xyz");
    expect(session.refreshToken).toBeNull();
    expect(session.claims.sub).toBe("usr_local_1");

    // The signed message uses the facade origin (must match SIWE_ORIGIN) + the
    // frozen statement + the local address, in the strict EIP-4361 shape.
    expect(verifyPayload.message).toContain(
      "127.0.0.1:54321 wants you to sign in with your Ethereum account:",
    );
    expect(verifyPayload.message).toContain(ADDRESS);
    expect(verifyPayload.message).toContain("Sign in to Copilot");
    expect(verifyPayload.message).toContain("URI: http://127.0.0.1:54321");
    expect(verifyPayload.message).toContain("Chain ID: 1");
    expect(typeof verifyPayload.signature).toBe("string");
    expect(verifyPayload.signature).toMatch(/^0x/);
  });

  it("raises a staged error when the nonce request fails", async () => {
    const fetchMock = routedFetch({
      "/v1/auth/siwe/nonce": () => new Response("nope", { status: 500 }),
    });
    await expect(
      runLocalLogin("org_acme", {
        facadeBaseUrl: FACADE,
        privateKey: PK,
        fetch: fetchMock,
      }),
    ).rejects.toBeInstanceOf(LocalLoginError);
  });

  it("raises a staged error when verify rejects the signature", async () => {
    const fetchMock = routedFetch({
      "/v1/auth/siwe/nonce": () => jsonResponse({ nonce: "abcd1234efgh5678" }),
      "/v1/auth/siwe/verify": () =>
        jsonResponse({ detail: "signature_invalid" }, 400),
    });
    await expect(
      runLocalLogin("org_acme", {
        facadeBaseUrl: FACADE,
        privateKey: PK,
        fetch: fetchMock,
      }),
    ).rejects.toMatchObject({ stage: "verify" });
  });

  it("rejects an MFA-required local identity", async () => {
    const fetchMock = routedFetch({
      "/v1/auth/siwe/nonce": () => jsonResponse({ nonce: "abcd1234efgh5678" }),
      "/v1/auth/siwe/verify": () =>
        jsonResponse({ ...HANDOFF, requires_mfa: true }),
    });
    await expect(
      runLocalLogin("org_acme", {
        facadeBaseUrl: FACADE,
        privateKey: PK,
        fetch: fetchMock,
      }),
    ).rejects.toMatchObject({ stage: "mfa" });
  });
});
