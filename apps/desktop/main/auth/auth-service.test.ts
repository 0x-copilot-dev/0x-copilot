// @vitest-environment node
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthAuditEntry, AuthAuditEvent, AuthAuditLog } from "./audit-log";
import { AuthService, type AuthSession } from "./index";
import type { runGoogleLogin } from "./google-login";
import type { runWalletLogin } from "./wallet-login";
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

  it("getSession drops a persisted session the facade rejects with 401 (fail closed)", async () => {
    // Persist a session (simulating the leftover dev-mint / stale bearer).
    const persist = new AuthService({
      mode: "dev-mint",
      facadeBaseUrl: "http://127.0.0.1:8200",
      userDataDir: tmp,
      safeStorage: fakeSafeStorage(),
      openExternal: async () => {},
      fetch: devMintFetch("stale-bearer"),
    });
    await persist.signIn("org_acme");

    // Reboot: a fresh service reads the on-disk session, then validates it
    // against the facade — which now rejects the stale bearer with 401.
    const probe = vi.fn(
      async () => new Response("", { status: 401 }),
    ) as unknown as typeof fetch;
    const reboot = new AuthService({
      mode: "dev-mint",
      facadeBaseUrl: "http://127.0.0.1:8200",
      userDataDir: tmp,
      safeStorage: fakeSafeStorage(),
      openExternal: async () => {},
      fetch: probe,
    });
    expect(await reboot.getSession("org_acme")).toBeNull();
    expect(probe).toHaveBeenCalled();
    // Cleared on disk: a second lookup is null too.
    expect(await reboot.getSession("org_acme")).toBeNull();
  });

  it("getSession keeps an unexpired session when the facade is unreachable (non-401)", async () => {
    const persist = new AuthService({
      mode: "dev-mint",
      facadeBaseUrl: "http://127.0.0.1:8200",
      userDataDir: tmp,
      safeStorage: fakeSafeStorage(),
      openExternal: async () => {},
      fetch: devMintFetch("good-bearer"),
    });
    await persist.signIn("org_acme");

    const netErr = vi.fn(async () => {
      throw new Error("ECONNREFUSED");
    }) as unknown as typeof fetch;
    const reboot = new AuthService({
      mode: "dev-mint",
      facadeBaseUrl: "http://127.0.0.1:8200",
      userDataDir: tmp,
      safeStorage: fakeSafeStorage(),
      openExternal: async () => {},
      fetch: netErr,
    });
    const out = await reboot.getSession("org_acme");
    expect(out).not.toBeNull();
    expect(out?.email).toBe("sarah@acme.test");
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

  function walletSession(bearer: string, sub = "usr_w1"): AuthSession {
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

  it("signInWithWallet persists the session, audits success, and getBearer serves it", async () => {
    const audit = memoryAudit();
    const flow = vi.fn(
      async (): Promise<AuthSession> => walletSession("w-bearer-1"),
    ) as unknown as typeof runWalletLogin;
    const service = new AuthService({
      mode: "dev-mint",
      facadeBaseUrl: "http://127.0.0.1:8200",
      userDataDir: tmp,
      safeStorage: fakeSafeStorage(),
      openExternal: async () => {},
      fetch: vi.fn() as unknown as typeof fetch,
      authAudit: audit,
      walletLoginFlow: flow,
    });

    const session = await service.signInWithWallet("org_acme");
    expect(session.workspaceId).toBe("org_acme");
    expect(session.email).toBe("sarah@acme.test");
    expect(session.displayName).toBe("Sarah Chen");

    // Flow received the workspace and the configured facade base URL.
    const flowMock = flow as unknown as { mock: { calls: unknown[][] } };
    expect(flowMock.mock.calls[0][0]).toBe("org_acme");
    expect(
      (flowMock.mock.calls[0][1] as { facadeBaseUrl: string }).facadeBaseUrl,
    ).toBe("http://127.0.0.1:8200");

    expect(await service.getBearer("org_acme")).toBe("w-bearer-1");
    expect(audit.events).toEqual([
      {
        kind: "sign-in-success",
        workspaceId: "org_acme",
        sub: "usr_w1",
        mode: "wallet",
      },
    ]);
  });

  it("signInWithWallet failure (e.g. MFA-pending refusal) audits and rethrows", async () => {
    const audit = memoryAudit();
    const flow = vi.fn(async (): Promise<AuthSession> => {
      throw new Error(
        "this account requires multi-factor authentication — sign in via the web app to complete MFA",
      );
    }) as unknown as typeof runWalletLogin;
    const service = new AuthService({
      mode: "dev-mint",
      facadeBaseUrl: "http://127.0.0.1:8200",
      userDataDir: tmp,
      safeStorage: fakeSafeStorage(),
      openExternal: async () => {},
      fetch: vi.fn() as unknown as typeof fetch,
      authAudit: audit,
      walletLoginFlow: flow,
    });

    await expect(service.signInWithWallet("org_acme")).rejects.toThrow(
      /multi-factor authentication/u,
    );
    expect(await service.getSession("org_acme")).toBeNull();
    expect(audit.events).toEqual([
      {
        kind: "sign-in-failure",
        workspaceId: "org_acme",
        mode: "wallet",
        reason:
          "this account requires multi-factor authentication — sign in via the web app to complete MFA",
      },
    ]);
  });

  it("a second signInWithWallet cancels the pending first and replaces the stored session", async () => {
    const audit = memoryAudit();
    let cancelled = 0;
    let call = 0;
    const flow = vi.fn(
      (workspaceId: string, deps: Parameters<typeof runWalletLogin>[1]) => {
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
        return Promise.resolve(walletSession("w-bearer-2", "usr_w2"));
      },
    ) as unknown as typeof runWalletLogin;

    const service = new AuthService({
      mode: "dev-mint",
      facadeBaseUrl: "http://127.0.0.1:8200",
      userDataDir: tmp,
      safeStorage: fakeSafeStorage(),
      openExternal: async () => {},
      fetch: vi.fn() as unknown as typeof fetch,
      authAudit: audit,
      walletLoginFlow: flow,
    });

    const firstError = service
      .signInWithWallet("org_acme")
      .then(() => null)
      .catch((err: unknown) => err);
    await Promise.resolve();
    const second = await service.signInWithWallet("org_acme");

    expect(cancelled).toBe(1);
    expect(String(await firstError)).toMatch(/closed before redirect/u);
    expect(second.workspaceId).toBe("org_acme");
    expect(await service.getBearer("org_acme")).toBe("w-bearer-2");
    const kinds = audit.events.map((e) => e.kind).sort();
    expect(kinds).toEqual(["sign-in-failure", "sign-in-success"]);
  });

  it("a wallet sign-in cancels a pending Google sign-in (newest click wins across modes)", async () => {
    let googleCancelled = 0;
    const googleFlow = vi.fn(
      (_workspaceId: string, deps: Parameters<typeof runGoogleLogin>[1]) =>
        new Promise<AuthSession>((_resolve, reject) => {
          deps.onCancelAvailable?.(() => {
            googleCancelled += 1;
            reject(new Error("loopback server closed before redirect"));
          });
        }),
    ) as unknown as typeof runGoogleLogin;
    const walletFlow = vi.fn(
      async (): Promise<AuthSession> => walletSession("w-bearer-x"),
    ) as unknown as typeof runWalletLogin;

    const service = new AuthService({
      mode: "dev-mint",
      facadeBaseUrl: "http://127.0.0.1:8200",
      userDataDir: tmp,
      safeStorage: fakeSafeStorage(),
      openExternal: async () => {},
      fetch: vi.fn() as unknown as typeof fetch,
      googleLoginFlow: googleFlow,
      walletLoginFlow: walletFlow,
    });

    const googleError = service
      .signInWithGoogle("org_acme")
      .then(() => null)
      .catch((err: unknown) => err);
    await Promise.resolve();
    const walletDone = await service.signInWithWallet("org_acme");

    expect(googleCancelled).toBe(1);
    expect(String(await googleError)).toMatch(/closed before redirect/u);
    expect(walletDone.workspaceId).toBe("org_acme");
    expect(await service.getBearer("org_acme")).toBe("w-bearer-x");
  });

  it("signInWithWallet replaces a previous dev-mint session on disk", async () => {
    const service = new AuthService({
      mode: "dev-mint",
      facadeBaseUrl: "http://127.0.0.1:8200",
      userDataDir: tmp,
      safeStorage: fakeSafeStorage(),
      openExternal: async () => {},
      fetch: devMintFetch("dev-bearer"),
      walletLoginFlow: vi.fn(
        async (): Promise<AuthSession> => walletSession("w-bearer-3"),
      ) as unknown as typeof runWalletLogin,
    });

    await service.signIn("org_acme");
    expect(await service.getBearer("org_acme")).toBe("dev-bearer");
    await service.signInWithWallet("org_acme");
    expect(await service.getBearer("org_acme")).toBe("w-bearer-3");
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

  describe("sign-out auditing — user-initiated vs. eviction", () => {
    // signOutUserInitiated is exactly what the authSignOut IPC handler is wired
    // to (see main/index.ts). Auditing it here proves the user-initiated
    // sign-out path records one 'sign-out' row.
    it("signOutUserInitiated (the IPC sign-out path) appends exactly one 'sign-out' audit row", async () => {
      const audit = memoryAudit();
      const service = new AuthService({
        mode: "dev-mint",
        facadeBaseUrl: "http://127.0.0.1:8200",
        userDataDir: tmp,
        safeStorage: fakeSafeStorage(),
        openExternal: async () => {},
        fetch: devMintFetch("bearer-1"),
        authAudit: audit,
      });
      await service.signIn("org_acme");
      await service.signOutUserInitiated("org_acme");

      // dev-mint signIn does not audit, so this is the only event.
      expect(audit.events).toEqual([
        { kind: "sign-out", workspaceId: "org_acme" },
      ]);
      // The session really is gone (and the null-session getSession below
      // returns early without calling signOut, so no extra row is appended).
      expect(await service.getSession("org_acme")).toBeNull();
      expect(audit.events.filter((e) => e.kind === "sign-out")).toHaveLength(1);
    });

    it("getSession dropping a stale (401) session appends NO 'sign-out' audit row (eviction is not a user sign-out)", async () => {
      // Persist a session, then reboot into a service whose facade rejects the
      // stored bearer with 401 — getSession evicts it via the raw signOut.
      const persist = new AuthService({
        mode: "dev-mint",
        facadeBaseUrl: "http://127.0.0.1:8200",
        userDataDir: tmp,
        safeStorage: fakeSafeStorage(),
        openExternal: async () => {},
        fetch: devMintFetch("stale-bearer"),
      });
      await persist.signIn("org_acme");

      const audit = memoryAudit();
      const probe = vi.fn(
        async () => new Response("", { status: 401 }),
      ) as unknown as typeof fetch;
      const reboot = new AuthService({
        mode: "dev-mint",
        facadeBaseUrl: "http://127.0.0.1:8200",
        userDataDir: tmp,
        safeStorage: fakeSafeStorage(),
        openExternal: async () => {},
        fetch: probe,
        authAudit: audit,
      });

      expect(await reboot.getSession("org_acme")).toBeNull();
      expect(probe).toHaveBeenCalled();
      expect(audit.events.filter((e) => e.kind === "sign-out")).toEqual([]);
    });

    it("the bare signOut mechanism appends NO 'sign-out' audit row", async () => {
      // Locks the invariant that eviction reuse of signOut() can never emit a
      // user sign-out event, independent of the getSession wiring above.
      const audit = memoryAudit();
      const service = new AuthService({
        mode: "dev-mint",
        facadeBaseUrl: "http://127.0.0.1:8200",
        userDataDir: tmp,
        safeStorage: fakeSafeStorage(),
        openExternal: async () => {},
        fetch: devMintFetch("bearer-1"),
        authAudit: audit,
      });
      await service.signIn("org_acme");
      await service.signOut("org_acme");
      expect(audit.events.filter((e) => e.kind === "sign-out")).toEqual([]);
    });
  });
});
