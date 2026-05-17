// @vitest-environment node
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthService } from "./index";
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
