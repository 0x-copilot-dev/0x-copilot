// @vitest-environment node
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";

import { describe, expect, it, vi } from "vitest";

import type { LoopbackHandoff, LoopbackHandoffHandle } from "./loopback-server";
import { WalletLoginError, runWalletLogin } from "./wallet-login";

interface FakeLoopback {
  readonly factory: ReturnType<typeof vi.fn>;
  readonly handle: LoopbackHandoffHandle & {
    armState: ReturnType<typeof vi.fn>;
    close: ReturnType<typeof vi.fn>;
  };
  resolveHandoff(handoff: LoopbackHandoff): void;
  rejectHandoff(err: Error): void;
}

function makeFakeLoopback(): FakeLoopback {
  let resolveHandoff: (value: LoopbackHandoff) => void = () => {};
  let rejectHandoff: (err: Error) => void = () => {};
  const handoffPromise = new Promise<LoopbackHandoff>((resolve, reject) => {
    resolveHandoff = resolve;
    rejectHandoff = reject;
  });
  const handle = {
    port: 43112,
    redirectUri: "http://127.0.0.1:43112/wallet/cb",
    handoffPromise,
    armState: vi.fn(),
    close: vi.fn(),
  };
  return {
    factory: vi.fn(async () => handle),
    handle,
    resolveHandoff,
    rejectHandoff,
  };
}

function walletHandoff(
  overrides: Partial<LoopbackHandoff> = {},
): LoopbackHandoff {
  return {
    bearerToken: "bearer-wallet-1",
    userId: "usr_w1",
    sessionId: "ses_w1",
    expiresAt: new Date(1_800_000_000_000).toISOString(),
    requiresMfa: false,
    returnTo: null,
    state: "st-wallet",
    ...overrides,
  };
}

const PROFILE = {
  user_id: "usr_w1",
  email: "sarah@acme.test",
  display_name: "Sarah Chen",
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

// Routes fetch calls by URL prefix — the wallet flow only has the single
// profile hop (nonce/verify run inside the wallet page, not here).
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

describe("runWalletLogin — happy path", () => {
  it("loopback armed with state → browser at /wallet.html?handoff= → handoff → profile-enriched session", async () => {
    const loopback = makeFakeLoopback();
    const opened: string[] = [];
    const fetchMock = routedFetch({
      "/v1/me/profile": (_url, init) => {
        const headers = init?.headers as Record<string, string>;
        expect(headers.authorization).toBe("Bearer bearer-wallet-1");
        return jsonResponse(PROFILE);
      },
    });

    const session = await runWalletLogin("org_acme", {
      facadeBaseUrl: "http://127.0.0.1:8200/",
      openExternal: async (url) => {
        opened.push(url);
        loopback.resolveHandoff(walletHandoff());
      },
      fetch: fetchMock,
      loopback: loopback.factory as never,
      generateState: () => "st-wallet",
    });

    // The loopback was created armed with the minted state.
    const factoryArgs = loopback.factory.mock.calls[0][0] as {
      expectedState: string;
      callbackPath: string;
    };
    expect(factoryArgs.expectedState).toBe("st-wallet");
    expect(factoryArgs.callbackPath).toBe("/wallet/cb");

    // The browser opened the facade-served wallet page with a loopback
    // handoff target that carries the state (it must round-trip).
    expect(opened).toHaveLength(1);
    const pageUrl = new URL(opened[0]);
    expect(pageUrl.origin).toBe("http://127.0.0.1:8200");
    expect(pageUrl.pathname).toBe("/wallet.html");
    const handoffTarget = new URL(pageUrl.searchParams.get("handoff") ?? "");
    expect(handoffTarget.origin).toBe("http://127.0.0.1:43112");
    expect(handoffTarget.pathname).toBe("/wallet/cb");
    expect(handoffTarget.searchParams.get("state")).toBe("st-wallet");

    expect(session.accessToken).toBe("bearer-wallet-1");
    expect(session.refreshToken).toBeNull();
    expect(session.idToken).toBeNull();
    expect(session.expiresAt).toBe(1_800_000_000_000);
    expect(session.claims).toEqual({
      sub: "usr_w1",
      email: "sarah@acme.test",
      name: "Sarah Chen",
      workspaceId: "org_acme",
    });
    expect(loopback.handle.close).toHaveBeenCalled();
  });

  it("tolerates a failing profile fetch — claims fall back to ids only", async () => {
    const loopback = makeFakeLoopback();
    const fetchMock = routedFetch({
      "/v1/me/profile": () => new Response("boom", { status: 500 }),
    });

    const session = await runWalletLogin("org_acme", {
      facadeBaseUrl: "http://127.0.0.1:8200",
      openExternal: async () => {
        loopback.resolveHandoff(walletHandoff());
      },
      fetch: fetchMock,
      loopback: loopback.factory as never,
    });

    expect(session.claims).toEqual({
      sub: "usr_w1",
      email: null,
      name: null,
      workspaceId: "org_acme",
    });
  });

  it("falls back to clock()+1h when the handoff expires_at is unparsable", async () => {
    const loopback = makeFakeLoopback();
    const fetchMock = routedFetch({
      "/v1/me/profile": () => jsonResponse(PROFILE),
    });

    const session = await runWalletLogin("org_acme", {
      facadeBaseUrl: "http://127.0.0.1:8200",
      openExternal: async () => {
        loopback.resolveHandoff(walletHandoff({ expiresAt: "not-a-date" }));
      },
      fetch: fetchMock,
      loopback: loopback.factory as never,
      clock: () => 1_000_000,
    });

    expect(session.expiresAt).toBe(1_000_000 + 60 * 60 * 1000);
  });
});

describe("runWalletLogin — failure paths", () => {
  it("openExternal failure throws stage 'open' and closes the loopback", async () => {
    const loopback = makeFakeLoopback();
    const attempt = runWalletLogin("org_acme", {
      facadeBaseUrl: "http://127.0.0.1:8200",
      openExternal: vi.fn(async () => {
        throw new Error("no default browser");
      }),
      fetch: vi.fn() as unknown as typeof fetch,
      loopback: loopback.factory as never,
    });
    await expect(attempt).rejects.toThrow(/could not open the system browser/u);
    const err = await attempt.catch((e: unknown) => e);
    expect(err).toBeInstanceOf(WalletLoginError);
    expect((err as WalletLoginError).stage).toBe("open");
    expect(loopback.handle.close).toHaveBeenCalled();
  });

  it("user cancel / timeout rejects with stage 'redirect'", async () => {
    const loopback = makeFakeLoopback();
    const attempt = runWalletLogin("org_acme", {
      facadeBaseUrl: "http://127.0.0.1:8200",
      openExternal: async () => {
        loopback.rejectHandoff(new Error("loopback redirect timed out"));
      },
      fetch: vi.fn() as unknown as typeof fetch,
      loopback: loopback.factory as never,
    });
    await expect(attempt).rejects.toThrow(/loopback redirect timed out/u);
    const err = await attempt.catch((e: unknown) => e);
    expect((err as WalletLoginError).stage).toBe("redirect");
    expect(loopback.handle.close).toHaveBeenCalled();
  });

  it("an invalid handoff (state mismatch at the loopback) rejects with stage 'redirect'", async () => {
    const loopback = makeFakeLoopback();
    const attempt = runWalletLogin("org_acme", {
      facadeBaseUrl: "http://127.0.0.1:8200",
      openExternal: async () => {
        loopback.rejectHandoff(new Error("wallet handoff state mismatch"));
      },
      fetch: vi.fn() as unknown as typeof fetch,
      loopback: loopback.factory as never,
    });
    await expect(attempt).rejects.toThrow(/state mismatch/u);
    const err = await attempt.catch((e: unknown) => e);
    expect((err as WalletLoginError).stage).toBe("redirect");
  });

  it("requires_mfa sessions are refused with stage 'mfa'", async () => {
    const loopback = makeFakeLoopback();
    const attempt = runWalletLogin("org_acme", {
      facadeBaseUrl: "http://127.0.0.1:8200",
      openExternal: async () => {
        loopback.resolveHandoff(walletHandoff({ requiresMfa: true }));
      },
      fetch: vi.fn() as unknown as typeof fetch,
      loopback: loopback.factory as never,
    });
    await expect(attempt).rejects.toThrow(/multi-factor authentication/u);
    const err = await attempt.catch((e: unknown) => e);
    expect((err as WalletLoginError).stage).toBe("mfa");
    expect(loopback.handle.close).toHaveBeenCalled();
  });

  it("exposes a cancel hook that closes the loopback", async () => {
    const loopback = makeFakeLoopback();
    let cancel: (() => void) | null = null;
    const attempt = runWalletLogin("org_acme", {
      facadeBaseUrl: "http://127.0.0.1:8200",
      openExternal: async () => {
        // Simulate "second sign-in replaces the first": cancel while the
        // flow is parked on the loopback handoff promise.
        expect(cancel).not.toBeNull();
        cancel?.();
        loopback.rejectHandoff(
          new Error("loopback server closed before redirect"),
        );
      },
      fetch: vi.fn() as unknown as typeof fetch,
      loopback: loopback.factory as never,
      onCancelAvailable: (c) => {
        cancel = c;
      },
    });
    await expect(attempt).rejects.toThrow(/closed before redirect/u);
    expect(loopback.handle.close).toHaveBeenCalled();
  });
});

// End-to-end over real HTTP: REAL loopback server (default
// awaitLoopbackHandoff, random port + conflict retry), a stub facade served
// by node:http for the profile hop, and a fake "system browser" that plays
// the wallet page: parse ?handoff=, validate it is loopback, append the
// session fields to its existing query (exactly what the frontend's
// buildHandoffRedirectUrl does) and GET it. Only the wallet/SIWE hop itself
// is simulated.
describe("runWalletLogin — integration against a stub facade + wallet page", () => {
  it("full round-trip: wallet page → loopback bearer handoff → profile", async () => {
    const facade: Server = createServer((req, res) => {
      const url = new URL(req.url ?? "/", "http://127.0.0.1");
      if (url.pathname === "/v1/me/profile") {
        expect(req.headers.authorization).toBe("Bearer bearer-e2e-wallet");
        res.setHeader("content-type", "application/json");
        res.end(
          JSON.stringify({
            user_id: "usr_e2e",
            email: "e2e@acme.test",
            display_name: "E2E Wallet User",
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
      const session = await runWalletLogin("org_acme", {
        facadeBaseUrl: `http://127.0.0.1:${facadePort}`,
        // The fake system browser: land on the wallet page URL, do what
        // the page does after a successful SIWE verify — redirect to the
        // loopback handoff target with the session in the query.
        openExternal: async (walletPageUrl) => {
          const pageUrl = new URL(walletPageUrl);
          expect(pageUrl.pathname).toBe("/wallet.html");
          const handoff = new URL(pageUrl.searchParams.get("handoff") ?? "");
          expect(handoff.hostname).toBe("127.0.0.1");
          expect(handoff.protocol).toBe("http:");
          handoff.searchParams.set("bearer_token", "bearer-e2e-wallet");
          handoff.searchParams.set("user_id", "usr_e2e");
          handoff.searchParams.set("session_id", "ses_e2e");
          handoff.searchParams.set(
            "expires_at",
            new Date(Date.now() + 3600_000).toISOString(),
          );
          handoff.searchParams.set("requires_mfa", "false");
          const landing = await fetch(handoff.toString());
          expect(landing.status).toBe(200);
        },
        timeoutMs: 10_000,
      });

      expect(session.accessToken).toBe("bearer-e2e-wallet");
      expect(session.claims).toEqual({
        sub: "usr_e2e",
        email: "e2e@acme.test",
        name: "E2E Wallet User",
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

  it("a forged redirect with the wrong state is rejected end-to-end", async () => {
    const attempt = runWalletLogin("org_acme", {
      facadeBaseUrl: "http://127.0.0.1:9",
      openExternal: async (walletPageUrl) => {
        const pageUrl = new URL(walletPageUrl);
        const handoff = new URL(pageUrl.searchParams.get("handoff") ?? "");
        handoff.searchParams.set("state", "forged-state");
        handoff.searchParams.set("bearer_token", "stolen-bearer");
        handoff.searchParams.set("user_id", "usr_evil");
        handoff.searchParams.set("session_id", "ses_evil");
        handoff.searchParams.set("expires_at", new Date().toISOString());
        handoff.searchParams.set("requires_mfa", "false");
        const landing = await fetch(handoff.toString());
        expect(landing.status).toBe(400);
      },
      timeoutMs: 10_000,
    });
    await expect(attempt).rejects.toThrow(/wallet handoff state mismatch/u);
    const err = await attempt.catch((e: unknown) => e);
    expect((err as WalletLoginError).stage).toBe("redirect");
  });
});
