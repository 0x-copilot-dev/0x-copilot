// @vitest-environment node
import { createHash } from "node:crypto";

import { describe, expect, it, vi } from "vitest";

import type { LoopbackHandle } from "./loopback-server";
import { OidcClient } from "./oidc-client";

function makeLoopback(code: string, state: string) {
  const handle: LoopbackHandle = {
    port: 12345,
    redirectUri: "http://127.0.0.1:12345/cb",
    codePromise: Promise.resolve({ code, state }),
    armState: vi.fn(),
    close: vi.fn(),
  };
  return vi.fn(async () => handle);
}

function fakeFetchOk(body: unknown): typeof fetch {
  return vi.fn(
    async () =>
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
  ) as unknown as typeof fetch;
}

function base64url(buf: Buffer): string {
  return buf
    .toString("base64")
    .replace(/=+$/u, "")
    .replace(/\+/gu, "-")
    .replace(/\//gu, "_");
}

describe("OidcClient — dev-mint mode", () => {
  it("POSTs to /v1/dev/identity/mint and returns a session", async () => {
    const futureIso = new Date(Date.now() + 60 * 60 * 1000).toISOString();
    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            bearer: "dev_bearer_abc",
            expires_at: futureIso,
            persona_slug: "sarah_acme",
            identity: {
              org_id: "org_acme",
              user_id: "usr_sarah",
              display_name: "Sarah",
              primary_email: "sarah@acme.test",
            },
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
    ) as unknown as typeof fetch;

    const client = new OidcClient({
      mode: "dev-mint",
      facadeBaseUrl: "http://127.0.0.1:8200",
      devPersonaSlug: "sarah_acme",
      fetch: fetchMock,
    });
    const session = await client.signIn("org_acme");
    expect(session.accessToken).toBe("dev_bearer_abc");
    expect(session.claims.workspaceId).toBe("org_acme");
    expect(session.claims.sub).toBe("usr_sarah");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8200/v1/dev/identity/mint",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("throws when the mint endpoint returns non-2xx", async () => {
    const fetchMock = vi.fn(
      async () => new Response("nope", { status: 503 }),
    ) as unknown as typeof fetch;
    const client = new OidcClient({
      mode: "dev-mint",
      facadeBaseUrl: "http://127.0.0.1:8200",
      fetch: fetchMock,
    });
    await expect(client.signIn("org_acme")).rejects.toThrow(/dev-mint failed/u);
  });
});

describe("OidcClient — oidc mode PKCE", () => {
  it("generates an S256 PKCE challenge from a base64url-encoded verifier", async () => {
    // Use a deterministic random to inspect the computed challenge.
    const randomVerifier = Buffer.alloc(64, 0xab);
    const randomState = Buffer.alloc(32, 0xcd);
    const callCount = { state: 0 };
    const fakeRandom = (size: number): Buffer => {
      callCount.state += 1;
      return callCount.state === 1 ? randomState : randomVerifier;
    };

    let capturedAuthUrl = "";
    const openExternal = vi.fn(async (url: string) => {
      capturedAuthUrl = url;
    });

    const expectedState = base64url(randomState);
    const expectedVerifier = base64url(randomVerifier);
    const expectedChallenge = base64url(
      createHash("sha256").update(expectedVerifier).digest(),
    );

    const fetchMock = fakeFetchOk({
      access_token: "at",
      refresh_token: "rt",
      expires_in: 3600,
    });

    const client = new OidcClient({
      mode: "oidc",
      facadeBaseUrl: "http://127.0.0.1:8200",
      oidc: {
        issuer: "https://idp.example",
        authorizationEndpoint: "https://idp.example/authorize",
        tokenEndpoint: "https://idp.example/token",
        clientId: "atlas",
        scopes: ["openid", "profile"],
      },
      fetch: fetchMock,
      random: fakeRandom as unknown as typeof import("node:crypto").randomBytes,
      openExternal,
      loopback: makeLoopback(
        "the-code",
        expectedState,
      ) as unknown as typeof import("./loopback-server").awaitLoopbackCode,
    });

    const session = await client.signIn("org_acme");

    expect(session.accessToken).toBe("at");
    expect(session.refreshToken).toBe("rt");
    const u = new URL(capturedAuthUrl);
    expect(u.searchParams.get("response_type")).toBe("code");
    expect(u.searchParams.get("code_challenge_method")).toBe("S256");
    expect(u.searchParams.get("code_challenge")).toBe(expectedChallenge);
    expect(u.searchParams.get("state")).toBe(expectedState);
    expect(u.searchParams.get("scope")).toBe("openid profile");

    // Verify the token-exchange POST received the verifier and matching redirect.
    const exchangeCall = (
      fetchMock as unknown as { mock: { calls: unknown[][] } }
    ).mock.calls[0];
    expect(exchangeCall[0]).toBe("https://idp.example/token");
    const body = (exchangeCall[1] as RequestInit).body as string;
    expect(body).toContain(`code_verifier=${expectedVerifier}`);
    expect(body).toContain("grant_type=authorization_code");
    expect(body).toContain("code=the-code");
  });

  it("refresh uses refresh_token grant", async () => {
    const fetchMock = fakeFetchOk({
      access_token: "newAT",
      expires_in: 3600,
    });
    const client = new OidcClient({
      mode: "oidc",
      facadeBaseUrl: "http://127.0.0.1:8200",
      oidc: {
        issuer: "https://idp.example",
        authorizationEndpoint: "https://idp.example/authorize",
        tokenEndpoint: "https://idp.example/token",
        clientId: "atlas",
        scopes: ["openid"],
      },
      fetch: fetchMock,
    });

    const next = await client.refresh("org_acme", {
      accessToken: "old",
      refreshToken: "rt-1",
      idToken: null,
      expiresAt: Date.now() + 10_000,
      claims: { sub: "u1", email: null, name: null, workspaceId: "org_acme" },
    });

    expect(next.accessToken).toBe("newAT");
    const call = (fetchMock as unknown as { mock: { calls: unknown[][] } }).mock
      .calls[0];
    expect((call[1] as RequestInit).body).toContain("grant_type=refresh_token");
    expect((call[1] as RequestInit).body).toContain("refresh_token=rt-1");
  });

  it("shouldRefreshSoon returns true within the refresh window", () => {
    let now = 1_000_000;
    const client = new OidcClient({
      mode: "dev-mint",
      facadeBaseUrl: "x",
      clock: () => now,
    });
    const session = {
      accessToken: "x",
      refreshToken: null,
      idToken: null,
      expiresAt: now + 30_000,
      claims: { sub: "u", email: null, name: null, workspaceId: "w" },
    };
    expect(client.shouldRefreshSoon(session, 60_000)).toBe(true);
    expect(client.shouldRefreshSoon(session, 10_000)).toBe(false);
  });
});
