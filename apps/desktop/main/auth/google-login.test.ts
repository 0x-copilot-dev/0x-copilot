// @vitest-environment node
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";

import { describe, expect, it, vi } from "vitest";

import type { LoopbackCode, LoopbackHandle } from "./loopback-server";
import { GoogleLoginError, runGoogleLogin } from "./google-login";

interface FakeLoopback {
  readonly factory: ReturnType<typeof vi.fn>;
  readonly handle: LoopbackHandle & {
    armState: ReturnType<typeof vi.fn>;
    close: ReturnType<typeof vi.fn>;
  };
  resolveCode(code: LoopbackCode): void;
  rejectCode(err: Error): void;
}

function makeFakeLoopback(): FakeLoopback {
  let resolveCode: (value: LoopbackCode) => void = () => {};
  let rejectCode: (err: Error) => void = () => {};
  const codePromise = new Promise<LoopbackCode>((resolve, reject) => {
    resolveCode = resolve;
    rejectCode = reject;
  });
  const handle = {
    port: 43111,
    redirectUri: "http://127.0.0.1:43111/oidc/cb",
    codePromise,
    armState: vi.fn(),
    close: vi.fn(),
  };
  return {
    factory: vi.fn(async () => handle),
    handle,
    resolveCode,
    rejectCode,
  };
}

const HANDOFF = {
  user_id: "usr_g1",
  session_id: "ses_g1",
  bearer_token: "bearer-google-1",
  expires_at: new Date(1_800_000_000_000).toISOString(),
  return_to: "atlas-desktop",
  requires_mfa: false,
};

const PROFILE = {
  user_id: "usr_g1",
  email: "sarah@acme.test",
  display_name: "Sarah Chen",
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

// Routes fetch calls by URL prefix so each test controls the three hops
// (start, callback, profile) independently.
function routedFetch(
  routes: Record<string, (url: string, init?: RequestInit) => Response>,
): typeof fetch {
  return vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    for (const [prefix, respond] of Object.entries(routes)) {
      if (url.includes(prefix)) return respond(url, init);
    }
    throw new Error(`unrouted fetch: ${url}`);
  }) as unknown as typeof fetch;
}

describe("runGoogleLogin — happy path", () => {
  it("start → browser → loopback → handoff → profile-enriched session", async () => {
    const loopback = makeFakeLoopback();
    const opened: string[] = [];
    const fetchMock = routedFetch({
      "/v1/auth/oidc/google/start": (url) => {
        const u = new URL(url);
        expect(u.searchParams.get("redirect_uri")).toBe(
          loopback.handle.redirectUri,
        );
        expect(u.searchParams.get("format")).toBe("json");
        expect(u.searchParams.get("return_to")).toBe("atlas-desktop");
        return jsonResponse({
          auth_url: "https://accounts.google.com/o/oauth2/v2/auth?x=1",
          state: "st-123",
          expires_at: new Date(Date.now() + 600_000).toISOString(),
        });
      },
      "/v1/auth/oidc/callback": (url) => {
        const u = new URL(url);
        expect(u.searchParams.get("state")).toBe("st-123");
        expect(u.searchParams.get("code")).toBe("code-abc");
        return jsonResponse(HANDOFF);
      },
      "/v1/me/profile": (_url, init) => {
        const headers = init?.headers as Record<string, string>;
        expect(headers.authorization).toBe("Bearer bearer-google-1");
        return jsonResponse(PROFILE);
      },
    });

    const sessionPromise = runGoogleLogin("org_acme", {
      facadeBaseUrl: "http://127.0.0.1:8200/",
      openExternal: async (url) => {
        opened.push(url);
        loopback.resolveCode({ code: "code-abc", state: "st-123" });
      },
      fetch: fetchMock,
      loopback: loopback.factory as never,
      returnTo: "atlas-desktop",
    });

    const session = await sessionPromise;
    expect(loopback.handle.armState).toHaveBeenCalledWith("st-123");
    expect(opened).toEqual([
      "https://accounts.google.com/o/oauth2/v2/auth?x=1",
    ]);
    expect(session.accessToken).toBe("bearer-google-1");
    expect(session.refreshToken).toBeNull();
    expect(session.idToken).toBeNull();
    expect(session.expiresAt).toBe(1_800_000_000_000);
    expect(session.claims).toEqual({
      sub: "usr_g1",
      email: "sarah@acme.test",
      name: "Sarah Chen",
      workspaceId: "org_acme",
    });
    expect(loopback.handle.close).toHaveBeenCalled();
  });

  it("tolerates a failing profile fetch — claims fall back to ids only", async () => {
    const loopback = makeFakeLoopback();
    const fetchMock = routedFetch({
      "/v1/auth/oidc/google/start": () =>
        jsonResponse({
          auth_url: "https://idp/auth",
          state: "st",
          expires_at: new Date().toISOString(),
        }),
      "/v1/auth/oidc/callback": () => jsonResponse(HANDOFF),
      "/v1/me/profile": () => new Response("boom", { status: 500 }),
    });

    const session = await runGoogleLogin("org_acme", {
      facadeBaseUrl: "http://127.0.0.1:8200",
      openExternal: async () => {
        loopback.resolveCode({ code: "c", state: "st" });
      },
      fetch: fetchMock,
      loopback: loopback.factory as never,
    });

    expect(session.claims).toEqual({
      sub: "usr_g1",
      email: null,
      name: null,
      workspaceId: "org_acme",
    });
  });
});

describe("runGoogleLogin — failure paths", () => {
  it("start endpoint failure throws stage 'start' and closes the loopback", async () => {
    const loopback = makeFakeLoopback();
    const fetchMock = routedFetch({
      "/v1/auth/oidc/google/start": () =>
        new Response("provider disabled", { status: 404 }),
    });

    const attempt = runGoogleLogin("org_acme", {
      facadeBaseUrl: "http://127.0.0.1:8200",
      openExternal: vi.fn(async () => {}),
      fetch: fetchMock,
      loopback: loopback.factory as never,
    });
    await expect(attempt).rejects.toThrow(/google sign-in start failed: 404/u);
    await expect(attempt).rejects.toBeInstanceOf(GoogleLoginError);
    expect(loopback.handle.close).toHaveBeenCalled();
  });

  it("user cancel / timeout rejects with stage 'redirect'", async () => {
    const loopback = makeFakeLoopback();
    const fetchMock = routedFetch({
      "/v1/auth/oidc/google/start": () =>
        jsonResponse({
          auth_url: "https://idp/auth",
          state: "st",
          expires_at: new Date().toISOString(),
        }),
    });

    const attempt = runGoogleLogin("org_acme", {
      facadeBaseUrl: "http://127.0.0.1:8200",
      openExternal: async () => {
        loopback.rejectCode(new Error("loopback redirect timed out"));
      },
      fetch: fetchMock,
      loopback: loopback.factory as never,
    });
    await expect(attempt).rejects.toThrow(/loopback redirect timed out/u);
    const err = await attempt.catch((e: unknown) => e);
    expect((err as GoogleLoginError).stage).toBe("redirect");
    expect(loopback.handle.close).toHaveBeenCalled();
  });

  it("expired/replayed state (callback 400) throws a handoff error", async () => {
    const loopback = makeFakeLoopback();
    const fetchMock = routedFetch({
      "/v1/auth/oidc/google/start": () =>
        jsonResponse({
          auth_url: "https://idp/auth",
          state: "st",
          expires_at: new Date().toISOString(),
        }),
      "/v1/auth/oidc/callback": () =>
        new Response("state expired", { status: 400 }),
    });

    const attempt = runGoogleLogin("org_acme", {
      facadeBaseUrl: "http://127.0.0.1:8200",
      openExternal: async () => {
        loopback.resolveCode({ code: "c", state: "st" });
      },
      fetch: fetchMock,
      loopback: loopback.factory as never,
    });
    await expect(attempt).rejects.toThrow(
      /state invalid, expired, or replayed/u,
    );
    const err = await attempt.catch((e: unknown) => e);
    expect((err as GoogleLoginError).stage).toBe("handoff");
  });

  it("requires_mfa sessions are refused with stage 'mfa'", async () => {
    const loopback = makeFakeLoopback();
    const fetchMock = routedFetch({
      "/v1/auth/oidc/google/start": () =>
        jsonResponse({
          auth_url: "https://idp/auth",
          state: "st",
          expires_at: new Date().toISOString(),
        }),
      "/v1/auth/oidc/callback": () =>
        jsonResponse({ ...HANDOFF, requires_mfa: true }),
    });

    const attempt = runGoogleLogin("org_acme", {
      facadeBaseUrl: "http://127.0.0.1:8200",
      openExternal: async () => {
        loopback.resolveCode({ code: "c", state: "st" });
      },
      fetch: fetchMock,
      loopback: loopback.factory as never,
    });
    await expect(attempt).rejects.toThrow(/multi-factor authentication/u);
    const err = await attempt.catch((e: unknown) => e);
    expect((err as GoogleLoginError).stage).toBe("mfa");
  });

  it("exposes a cancel hook that closes the loopback", async () => {
    const loopback = makeFakeLoopback();
    let cancel: (() => void) | null = null;
    const fetchMock = routedFetch({
      "/v1/auth/oidc/google/start": () =>
        jsonResponse({
          auth_url: "https://idp/auth",
          state: "st",
          expires_at: new Date().toISOString(),
        }),
    });

    const attempt = runGoogleLogin("org_acme", {
      facadeBaseUrl: "http://127.0.0.1:8200",
      openExternal: async () => {
        // Simulate "second sign-in replaces the first": cancel while the
        // flow is parked on the loopback code promise.
        expect(cancel).not.toBeNull();
        cancel?.();
        loopback.rejectCode(
          new Error("loopback server closed before redirect"),
        );
      },
      fetch: fetchMock,
      loopback: loopback.factory as never,
      onCancelAvailable: (c) => {
        cancel = c;
      },
    });
    await expect(attempt).rejects.toThrow(/closed before redirect/u);
    expect(loopback.handle.close).toHaveBeenCalled();
  });
});

// End-to-end over real HTTP: REAL loopback server (default awaitLoopbackCode,
// random port + conflict retry), a stub facade served by node:http, and a
// fake "system browser" that follows the redirect_uri like Google would.
// Only the Google hop itself is simulated.
describe("runGoogleLogin — integration against a stub facade", () => {
  it("full round-trip: start (json) → browser redirect → loopback → handoff → profile", async () => {
    const seenStates: string[] = [];
    const facade: Server = createServer((req, res) => {
      const url = new URL(req.url ?? "/", "http://127.0.0.1");
      if (url.pathname === "/v1/auth/oidc/google/start") {
        const redirectUri = url.searchParams.get("redirect_uri") ?? "";
        expect(url.searchParams.get("format")).toBe("json");
        const authUrl = new URL("https://accounts.google.com/o/oauth2/v2/auth");
        authUrl.searchParams.set("redirect_uri", redirectUri);
        authUrl.searchParams.set("state", "st-e2e");
        res.setHeader("content-type", "application/json");
        res.end(
          JSON.stringify({
            auth_url: authUrl.toString(),
            state: "st-e2e",
            expires_at: new Date(Date.now() + 600_000).toISOString(),
          }),
        );
        return;
      }
      if (url.pathname === "/v1/auth/oidc/callback") {
        seenStates.push(url.searchParams.get("state") ?? "");
        expect(url.searchParams.get("code")).toBe("code-e2e");
        res.setHeader("content-type", "application/json");
        res.end(
          JSON.stringify({
            user_id: "usr_e2e",
            session_id: "ses_e2e",
            bearer_token: "bearer-e2e",
            expires_at: new Date(Date.now() + 3600_000).toISOString(),
            return_to: null,
            requires_mfa: false,
          }),
        );
        return;
      }
      if (url.pathname === "/v1/me/profile") {
        expect(req.headers.authorization).toBe("Bearer bearer-e2e");
        res.setHeader("content-type", "application/json");
        res.end(
          JSON.stringify({
            user_id: "usr_e2e",
            email: "e2e@acme.test",
            display_name: "E2E User",
          }),
        );
        return;
      }
      res.statusCode = 404;
      res.end();
    });
    await new Promise<void>((resolve) => {
      facade.listen(0, "127.0.0.1", resolve);
    });
    const facadePort = (facade.address() as AddressInfo).port;

    try {
      const session = await runGoogleLogin("org_acme", {
        facadeBaseUrl: `http://127.0.0.1:${facadePort}`,
        // The fake system browser: land on Google, immediately follow the
        // redirect back to the loopback exactly as the real one would.
        openExternal: async (authUrl) => {
          const u = new URL(authUrl);
          expect(u.origin).toBe("https://accounts.google.com");
          const redirectUri = u.searchParams.get("redirect_uri") ?? "";
          const state = u.searchParams.get("state") ?? "";
          const landing = await fetch(
            `${redirectUri}?state=${encodeURIComponent(state)}&code=code-e2e`,
          );
          expect(landing.status).toBe(200);
        },
        timeoutMs: 10_000,
      });

      expect(seenStates).toEqual(["st-e2e"]);
      expect(session.accessToken).toBe("bearer-e2e");
      expect(session.claims).toEqual({
        sub: "usr_e2e",
        email: "e2e@acme.test",
        name: "E2E User",
        workspaceId: "org_acme",
      });
    } finally {
      await new Promise<void>((resolve) => {
        facade.close(() => {
          resolve();
        });
      });
    }
  });
});
