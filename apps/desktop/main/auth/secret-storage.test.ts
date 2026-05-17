// @vitest-environment node
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SecretStorage, type SafeStorageLike } from "./secret-storage";

function makeFakeSafeStorage(available: boolean): SafeStorageLike {
  return {
    isEncryptionAvailable: () => available,
    encryptString: (plaintext: string) =>
      Buffer.concat([
        Buffer.from("ENCv1:", "utf-8"),
        Buffer.from(plaintext, "utf-8").map((b) => b ^ 0x42),
      ]),
    decryptString: (cipher: Buffer) => {
      const marker = Buffer.from("ENCv1:", "utf-8");
      const rest = cipher.subarray(marker.length);
      return Buffer.from(rest.map((b) => b ^ 0x42)).toString("utf-8");
    },
  };
}

describe("SecretStorage", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = mkdtempSync(join(tmpdir(), "atlas-secret-"));
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  it("round-trips a session for the active workspace", async () => {
    const storage = new SecretStorage({
      userDataDir: tmp,
      safeStorage: makeFakeSafeStorage(true),
    });
    storage.setActiveWorkspace("org_acme");
    await storage.set("org_acme", "backend", "facade", {
      accessToken: "ya29.token",
      claims: { sub: "u1" },
    });
    const out = (await storage.get("org_acme", "backend", "facade")) as Record<
      string,
      unknown
    >;
    expect(out.accessToken).toBe("ya29.token");
  });

  it("rejects reads for the non-active workspace", async () => {
    const audit = { warn: vi.fn() };
    const storage = new SecretStorage({
      userDataDir: tmp,
      safeStorage: makeFakeSafeStorage(true),
      audit,
    });
    storage.setActiveWorkspace("org_acme");
    await storage.set("org_acme", "backend", "facade", {
      accessToken: "secret",
    });

    // Switch active workspace and try to read the first workspace's secret.
    storage.setActiveWorkspace("org_contoso");
    const out = await storage.get("org_acme", "backend", "facade");
    expect(out).toBeNull();
    expect(audit.warn).toHaveBeenCalledWith(
      expect.stringContaining("gate rejected read"),
      expect.objectContaining({ requested: "org_acme", active: "org_contoso" }),
    );
  });

  it("rejects writes for the non-active workspace", async () => {
    const storage = new SecretStorage({
      userDataDir: tmp,
      safeStorage: makeFakeSafeStorage(true),
    });
    storage.setActiveWorkspace("org_acme");
    await expect(
      storage.set("org_contoso", "backend", "facade", { x: 1 }),
    ).rejects.toThrow(/active-workspace gate/u);
  });

  it("on-disk ciphertext does not contain the plaintext token", async () => {
    const storage = new SecretStorage({
      userDataDir: tmp,
      safeStorage: makeFakeSafeStorage(true),
    });
    storage.setActiveWorkspace("org_acme");
    const secret = "sk_super_secret_token_12345";
    await storage.set("org_acme", "backend", "facade", { accessToken: secret });

    // Walk the directory to find the .bin file and assert plaintext absent.
    const secretsRoot = join(tmp, "secrets", "org_acme", "backend");
    const { readdirSync } = await import("node:fs");
    const files = readdirSync(secretsRoot);
    expect(files.length).toBe(1);
    const raw = readFileSync(join(secretsRoot, files[0]));
    expect(raw.toString("utf-8")).not.toContain(secret);
    expect(raw.toString("utf-8")).toContain("ATLASv1:cipher:");
  });

  it("refuses to write plaintext when safeStorage is unavailable and fallback is disabled", async () => {
    const storage = new SecretStorage({
      userDataDir: tmp,
      safeStorage: makeFakeSafeStorage(false),
      allowPlaintextFallback: false,
    });
    storage.setActiveWorkspace("org_acme");
    await expect(
      storage.set("org_acme", "backend", "facade", { x: 1 }),
    ).rejects.toThrow(/refusing to write plaintext/u);
  });

  it("falls back to plaintext (with audit warn) when safeStorage unavailable and fallback enabled", async () => {
    const audit = { warn: vi.fn() };
    const storage = new SecretStorage({
      userDataDir: tmp,
      safeStorage: makeFakeSafeStorage(false),
      allowPlaintextFallback: true,
      audit,
    });
    storage.setActiveWorkspace("org_acme");
    await storage.set("org_acme", "backend", "facade", { v: 1 });
    expect(audit.warn).toHaveBeenCalledWith(
      expect.stringContaining("falling back to plaintext"),
    );
    const out = (await storage.get("org_acme", "backend", "facade")) as {
      v: number;
    };
    expect(out.v).toBe(1);
  });

  it("deleteWorkspaceSecrets removes only that workspace's directory tree", async () => {
    const storage = new SecretStorage({
      userDataDir: tmp,
      safeStorage: makeFakeSafeStorage(true),
    });
    storage.setActiveWorkspace("org_acme");
    await storage.set("org_acme", "backend", "facade", { a: 1 });
    storage.setActiveWorkspace("org_contoso");
    await storage.set("org_contoso", "backend", "facade", { a: 2 });

    await storage.deleteWorkspaceSecrets("org_acme");

    // Active workspace's data still readable.
    const stillThere = await storage.get("org_contoso", "backend", "facade");
    expect(stillThere).not.toBeNull();

    // Gone for org_acme even with gate flipped.
    storage.setActiveWorkspace("org_acme");
    const gone = await storage.get("org_acme", "backend", "facade");
    expect(gone).toBeNull();
  });

  it("rejects path-traversal-shaped workspaceIds", () => {
    const storage = new SecretStorage({
      userDataDir: tmp,
      safeStorage: makeFakeSafeStorage(true),
    });
    storage.setActiveWorkspace("foo/../bar");
    expect(() => storage.setActiveWorkspace("../escape")).not.toThrow();
    return expect(
      storage.set("../escape", "backend", "facade", { x: 1 }),
    ).rejects.toThrow(/invalid workspaceId/u);
  });

  it("returns null when active workspace is not set", async () => {
    const storage = new SecretStorage({
      userDataDir: tmp,
      safeStorage: makeFakeSafeStorage(true),
    });
    const out = await storage.get("org_acme", "backend", "facade");
    expect(out).toBeNull();
  });
});
