// @vitest-environment node
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthAuditEntry, AuthAuditEvent, AuthAuditLog } from "./audit-log";
import { AuthService, type AuthSession } from "./index";
import type { runGoogleLogin } from "./google-login";
import type { SafeStorageLike } from "./secret-storage";

function fakeSafeStorage(): SafeStorageLike {
  return {
    isEncryptionAvailable: () => true,
    encryptString: (plaintext: string) =>
      Buffer.concat([
        Buffer.from("ENC:", "utf-8"),
        Buffer.from(plaintext, "utf-8").map((b) => b ^ 0x55),
      ]),
    decryptString: (cipher: Buffer) =>
      Buffer.from(
        cipher.subarray(Buffer.byteLength("ENC:")).map((b) => b ^ 0x55),
      ).toString("utf-8"),
  };
}

function devMintFetch(bearer: string, expiresIn = 3600): typeof fetch {
  return vi.fn(
    async () =>
      new Response(
        JSON.stringify({
          bearer,
          expires_at: new Date(Date.now() + expiresIn * 1000).toISOString(),
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
}

describe("AuthService", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = mkdtempSync(join(tmpdir(), "atlas-auth-"));
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  it("signIn persists the session and getSession returns the renderer view", async () => {
    const service = new AuthService({
      mode: "dev-mint",
      facadeBaseUrl: "http://127.0.0.1:8200",
      userDataDir: tmp,
      safeStorage: fakeSafeStorage(),
      openExternal: async () => {},
      fetch: devMintFetch("bearer-1"),
    });
    const session = await service.signIn("org_acme");
    expect(session.workspaceId).toBe("org_acme");
    expect(session.email).toBe("sarah@acme.test");

    // getSession should hit the cache and return the same renderer view.
    const again = await service.getSession("org_acme");
    expect(again).not.toBeNull();
    expect(again?.email).toBe("sarah@acme.test");
  });

  it("getBearer returns null when no session is stored", async () => {
    const service = new AuthService({
      mode: "dev-mint",
      facadeBaseUrl: "http://127.0.0.1:8200",
      userDataDir: tmp,
      safeStorage: fakeSafeStorage(),
      openExternal: async () => {},
      fetch: vi.fn() as unknown as typeof fetch,
    });
    const out = await service.getBearer("org_acme");
    expect(out).toBeNull();
  });

  it("getBearer returns the stored access token when not near expiry", async () => {
    const service = new AuthService({
      mode: "dev-mint",
      facadeBaseUrl: "http://127.0.0.1:8200",
      userDataDir: tmp,
      safeStorage: fakeSafeStorage(),
      openExternal: async () => {},
      fetch: devMintFetch("bearer-token-X"),
    });
    await service.signIn("org_acme");
    const bearer = await service.getBearer("org_acme");
    expect(bearer).toBe("bearer-token-X");
  });

  it("signOut clears the cached session and on-disk file for the active workspace", async () => {
    const service = new AuthService({
      mode: "dev-mint",
      facadeBaseUrl: "http://127.0.0.1:8200",
      userDataDir: tmp,
      safeStorage: fakeSafeStorage(),
      openExternal: async () => {},
      fetch: devMintFetch("bearer-1"),
    });
    await service.signIn("org_acme");
    await service.signOut("org_acme");
    const after = await service.getSession("org_acme");
    expect(after).toBeNull();
  });

  function memoryAudit(): AuthAuditLog & { events: AuthAuditEvent[] } {
    const events: AuthAuditEvent[] = [];
    return {
      events,
      async append(event) {
        events.push(event);
      },
      async readAll(): Promise<readonly AuthAuditEntry[]> {
        return events.map((event) => ({ ts: "t", event }));
      },
    };
  }

  function googleSession(bearer: string, sub = "usr_g1"): AuthSession {
    return {
      idToken: null,
      accessToken: bearer,
      refreshToken: null,
      expiresAt: Date.now() + 3600_000,
      claims: {
        sub,
        email: "sarah@acme.test",
        name: "Sarah Chen",
        workspaceId: "org_acme",
      },
    };
  }

  it("signInWithGoogle persists the session, audits success, and getBearer serves it", async () => {
    const audit = memoryAudit();
    const flow = vi.fn(
      async (): Promise<AuthSession> => googleSession("g-bearer-1"),
    ) as unknown as typeof runGoogleLogin;
    const service = new AuthService({
      mode: "dev-mint",
      facadeBaseUrl: "http://127.0.0.1:8200",
      userDataDir: tmp,
      safeStorage: fakeSafeStorage(),
      openExternal: async () => {},
      fetch: vi.fn() as unknown as typeof fetch,
      authAudit: audit,
      googleLoginFlow: flow,
    });

    const session = await service.signInWithGoogle("org_acme");
    expect(session.workspaceId).toBe("org_acme");
    expect(session.email).toBe("sarah@acme.test");
    expect(session.displayName).toBe("Sarah Chen");

    // Flow received the workspace and the configured facade base URL.
    const flowMock = flow as unknown as { mock: { calls: unknown[][] } };
    expect(flowMock.mock.calls[0][0]).toBe("org_acme");
    expect(
      (flowMock.mock.calls[0][1] as { facadeBaseUrl: string }).facadeBaseUrl,
    ).toBe("http://127.0.0.1:8200");

    expect(await service.getBearer("org_acme")).toBe("g-bearer-1");
    expect(audit.events).toEqual([
      {
        kind: "sign-in-success",
        workspaceId: "org_acme",
        sub: "usr_g1",
        mode: "google",
      },
    ]);
  });

  it("signInWithGoogle failure audits a sign-in-failure and rethrows", async () => {
    const audit = memoryAudit();
    const flow = vi.fn(async (): Promise<AuthSession> => {
      throw new Error("loopback redirect timed out");
    }) as unknown as typeof runGoogleLogin;
    const service = new AuthService({
      mode: "dev-mint",
      facadeBaseUrl: "http://127.0.0.1:8200",
      userDataDir: tmp,
      safeStorage: fakeSafeStorage(),
      openExternal: async () => {},
      fetch: vi.fn() as unknown as typeof fetch,
      authAudit: audit,
      googleLoginFlow: flow,
    });

    await expect(service.signInWithGoogle("org_acme")).rejects.toThrow(
      /timed out/u,
    );
    expect(await service.getSession("org_acme")).toBeNull();
    expect(audit.events).toEqual([
      {
        kind: "sign-in-failure",
        workspaceId: "org_acme",
        mode: "google",
        reason: "loopback redirect timed out",
      },
    ]);
  });

  it("a second signInWithGoogle cancels the pending first and replaces the stored session", async () => {
    const audit = memoryAudit();
    let cancelled = 0;
    let call = 0;
    const flow = vi.fn(
      (workspaceId: string, deps: Parameters<typeof runGoogleLogin>[1]) => {
        call += 1;
        if (call === 1) {
          // First flow parks forever on the loopback until cancelled.
          return new Promise<AuthSession>((_resolve, reject) => {
            deps.onCancelAvailable?.(() => {
              cancelled += 1;
              reject(new Error("loopback server closed before redirect"));
            });
          });
        }
        return Promise.resolve(googleSession("g-bearer-2", "usr_g2"));
      },
    ) as unknown as typeof runGoogleLogin;

    const service = new AuthService({
      mode: "dev-mint",
      facadeBaseUrl: "http://127.0.0.1:8200",
      userDataDir: tmp,
      safeStorage: fakeSafeStorage(),
      openExternal: async () => {},
      fetch: vi.fn() as unknown as typeof fetch,
      authAudit: audit,
      googleLoginFlow: flow,
    });

    // Attach the rejection handler up-front so the cancellation of the
    // first flow never counts as an unhandled rejection.
    const firstError = service
      .signInWithGoogle("org_acme")
      .then(() => null)
      .catch((err: unknown) => err);
    // Let the first flow install its cancel hook.
    await Promise.resolve();
    const second = await service.signInWithGoogle("org_acme");

    expect(cancelled).toBe(1);
    expect(String(await firstError)).toMatch(/closed before redirect/u);
    expect(second.workspaceId).toBe("org_acme");
    expect(await service.getBearer("org_acme")).toBe("g-bearer-2");
    const kinds = audit.events.map((e) => e.kind).sort();
    expect(kinds).toEqual(["sign-in-failure", "sign-in-success"]);
  });

  it("signInWithGoogle replaces a previous dev-mint session on disk", async () => {
    const service = new AuthService({
      mode: "dev-mint",
      facadeBaseUrl: "http://127.0.0.1:8200",
      userDataDir: tmp,
      safeStorage: fakeSafeStorage(),
      openExternal: async () => {},
      fetch: devMintFetch("dev-bearer"),
      googleLoginFlow: vi.fn(
        async (): Promise<AuthSession> => googleSession("g-bearer-3"),
      ) as unknown as typeof runGoogleLogin,
    });

    await service.signIn("org_acme");
    expect(await service.getBearer("org_acme")).toBe("dev-bearer");
    await service.signInWithGoogle("org_acme");
    expect(await service.getBearer("org_acme")).toBe("g-bearer-3");
  });

  it("refresh re-mints in dev-mint mode", async () => {
    let counter = 0;
    const fetchMock = vi.fn(async () => {
      counter += 1;
      return new Response(
        JSON.stringify({
          bearer: `bearer-${counter}`,
          expires_at: new Date(Date.now() + 3600_000).toISOString(),
          persona_slug: "sarah_acme",
          identity: {
            org_id: "org_acme",
            user_id: "usr_sarah",
            display_name: "Sarah",
            primary_email: "sarah@acme.test",
          },
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }) as unknown as typeof fetch;

    const service = new AuthService({
      mode: "dev-mint",
      facadeBaseUrl: "http://127.0.0.1:8200",
      userDataDir: tmp,
      safeStorage: fakeSafeStorage(),
      openExternal: async () => {},
      fetch: fetchMock,
    });
    await service.signIn("org_acme");
    await service.refresh("org_acme");
    const bearer = await service.getBearer("org_acme");
    expect(bearer).toBe("bearer-2");
  });
});
