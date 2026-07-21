import { describe, expect, it, vi } from "vitest";

import { LocalLoginError, runLocalLogin } from "./local-login";

const FACADE = "http://127.0.0.1:54321";
const HOST_TOKEN = "host-secret-abc";

const HANDOFF = {
  user_id: "usr_local_1",
  org_id: "org_local_1",
  session_id: "sess_1",
  bearer_token: "bearer-local-xyz",
  expires_at: "2999-01-01T00:00:00Z",
  created: true,
};
const PROFILE = { display_name: "Local account", email: null };

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
  it("mints the device-account session with the host token", async () => {
    let mintHeaders: Record<string, string> = {};
    const fetchMock = routedFetch({
      "/v1/auth/local/session": (_url, init) => {
        mintHeaders = Object.fromEntries(
          Object.entries((init?.headers ?? {}) as Record<string, string>),
        );
        return jsonResponse(HANDOFF);
      },
      "/v1/me/profile": () => jsonResponse(PROFILE),
    });

    const session = await runLocalLogin("org_acme", {
      facadeBaseUrl: FACADE,
      hostToken: HOST_TOKEN,
      fetch: fetchMock,
    });

    // The ONE thing that authorizes the mint: the per-install host secret.
    expect(mintHeaders["x-enterprise-service-token"]).toBe(HOST_TOKEN);
    expect(session.accessToken).toBe("bearer-local-xyz");
    expect(session.refreshToken).toBeNull();
    expect(session.expiresAt).toBe(Date.parse(HANDOFF.expires_at));
    expect(session.claims.sub).toBe("usr_local_1");
  });

  it("fails closed without a host token — no network call at all", async () => {
    const fetchMock = vi.fn() as unknown as typeof fetch;
    await expect(
      runLocalLogin("org_acme", {
        facadeBaseUrl: FACADE,
        hostToken: "",
        fetch: fetchMock,
      }),
    ).rejects.toBeInstanceOf(LocalLoginError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("surfaces a mint rejection (401 wrong token) as LocalLoginError", async () => {
    const fetchMock = routedFetch({
      "/v1/auth/local/session": () =>
        new Response("invalid_host_token", { status: 401 }),
    });
    await expect(
      runLocalLogin("org_acme", {
        facadeBaseUrl: FACADE,
        hostToken: "wrong",
        fetch: fetchMock,
      }),
    ).rejects.toMatchObject({ stage: "mint" });
  });

  it("falls back to a default TTL when expires_at is unparseable", async () => {
    const NOW = 1_700_000_000_000;
    const fetchMock = routedFetch({
      "/v1/auth/local/session": () =>
        jsonResponse({ ...HANDOFF, expires_at: "not-a-date" }),
      "/v1/me/profile": () => jsonResponse(PROFILE),
    });
    const session = await runLocalLogin("org_acme", {
      facadeBaseUrl: FACADE,
      hostToken: HOST_TOKEN,
      fetch: fetchMock,
      clock: () => NOW,
    });
    expect(session.expiresAt).toBe(NOW + 60 * 60 * 1000);
  });
});
