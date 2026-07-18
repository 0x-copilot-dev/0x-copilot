// @vitest-environment node
import { mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SafeStorageLike } from "../auth/secret-storage";
import { GrantStore } from "./grant-store";

// XOR "cipher" — enough to prove encryption happened + round-trips, exactly
// like secret-storage.test.ts.
function makeFakeSafeStorage(available: boolean): SafeStorageLike {
  return {
    isEncryptionAvailable: () => available,
    encryptString: (plaintext: string) =>
      Buffer.concat([
        Buffer.from("ENC:", "utf-8"),
        Buffer.from(plaintext, "utf-8").map((b) => b ^ 0x42),
      ]),
    decryptString: (cipher: Buffer) => {
      const rest = cipher.subarray(Buffer.from("ENC:", "utf-8").length);
      return Buffer.from(rest.map((b) => b ^ 0x42)).toString("utf-8");
    },
  };
}

let idCounter = 0;
function seqUuid(): string {
  idCounter += 1;
  return `00000000-0000-4000-8000-${String(idCounter).padStart(12, "0")}`;
}

describe("GrantStore", () => {
  let tmp: string;

  beforeEach(() => {
    idCounter = 0;
    tmp = mkdtempSync(join(tmpdir(), "cap-grants-"));
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  function makeStore(available = true, allowPlaintextFallback = false) {
    return new GrantStore({
      userDataDir: tmp,
      safeStorage: makeFakeSafeStorage(available),
      allowPlaintextFallback,
      uuid: seqUuid,
      clock: () => 1000,
    });
  }

  it("creates an active grant and lists it", async () => {
    const store = makeStore();
    const grant = await store.create({
      root: "/Users/x/projects/atlas",
      mode: "read_write",
      label: "atlas",
    });
    expect(grant.status).toBe("active");
    expect(grant.root).toBe("/Users/x/projects/atlas");
    const all = await store.list();
    expect(all).toHaveLength(1);
    expect(all[0].grantId).toBe(grant.grantId);
  });

  it("rejects a non-absolute root without echoing the value", async () => {
    const store = makeStore();
    await expect(
      store.create({ root: "relative/path", mode: "read_only", label: "x" }),
    ).rejects.toThrow(/absolute path/u);
  });

  it("revoke marks the grant revoked and drops it from active views", async () => {
    const store = makeStore();
    const grant = await store.create({
      root: "/data/reports",
      mode: "read_only",
      label: "reports",
    });
    const revoked = await store.revoke(grant.grantId);
    expect(revoked?.status).toBe("revoked");

    const active = await store.listActive();
    expect(active).toHaveLength(0);
    const snapshot = await store.snapshotActive();
    expect(snapshot.grants).toHaveLength(0);
    // still present in the full list, just revoked
    const all = await store.list();
    expect(all).toHaveLength(1);
    expect(all[0].status).toBe("revoked");
  });

  it("revoke is idempotent and returns null for unknown ids", async () => {
    const store = makeStore();
    const grant = await store.create({
      root: "/data/a",
      mode: "read_only",
      label: "a",
    });
    const first = await store.revoke(grant.grantId);
    const second = await store.revoke(grant.grantId);
    expect(second?.status).toBe("revoked");
    expect(second?.updatedAt).toBe(first?.updatedAt);
    expect(
      await store.revoke("11111111-1111-4111-8111-111111111111"),
    ).toBeNull();
  });

  it("persists encrypted and round-trips across a fresh store instance", async () => {
    const store = makeStore();
    await store.create({
      root: "/home/me/Documents",
      mode: "read_write_no_delete",
      label: "Documents",
    });

    // Fresh instance reads the same file back.
    const reopened = new GrantStore({
      userDataDir: tmp,
      safeStorage: makeFakeSafeStorage(true),
    });
    const all = await reopened.list();
    expect(all).toHaveLength(1);
    expect(all[0].root).toBe("/home/me/Documents");
    expect(all[0].mode).toBe("read_write_no_delete");
  });

  it("on-disk blob is ciphertext and does not contain the host path", async () => {
    const store = makeStore();
    const root = "/home/secret-user/private-folder";
    await store.create({ root, mode: "read_only", label: "private-folder" });

    const dir = join(tmp, "capabilities");
    const files = readdirSync(dir);
    expect(files).toContain("grants.bin");
    const raw = readFileSync(join(dir, "grants.bin"));
    expect(raw.toString("utf-8")).toContain("ATLASCAPv1:cipher:");
    expect(raw.toString("utf-8")).not.toContain(root);
    expect(raw.toString("utf-8")).not.toContain("secret-user");
  });

  it("refuses to write plaintext when safeStorage is unavailable and fallback disabled", async () => {
    const store = makeStore(false, false);
    await expect(
      store.create({ root: "/data/x", mode: "read_only", label: "x" }),
    ).rejects.toThrow(/refusing to write plaintext/u);
  });

  it("allows a plaintext fallback (dev) with an audit warning", async () => {
    const audit = { warn: vi.fn() };
    const store = new GrantStore({
      userDataDir: tmp,
      safeStorage: makeFakeSafeStorage(false),
      allowPlaintextFallback: true,
      audit,
      uuid: seqUuid,
      clock: () => 1000,
    });
    await store.create({ root: "/data/x", mode: "read_only", label: "x" });
    expect(audit.warn).toHaveBeenCalledWith(
      expect.stringContaining("falling back to plaintext"),
    );
    const raw = readFileSync(join(tmp, "capabilities", "grants.bin"));
    expect(raw.toString("utf-8")).toContain("ATLASCAPv1:plaintext:");
  });

  it("snapshots are immutable and carry a fresh id each time", async () => {
    const store = makeStore();
    await store.create({ root: "/data/a", mode: "read_only", label: "a" });
    const s1 = await store.snapshotActive();
    const s2 = await store.snapshotActive();
    expect(s1.snapshotId).not.toBe(s2.snapshotId);
    expect(Object.isFrozen(s1.grants)).toBe(true);
    expect(Object.isFrozen(s1.grants[0])).toBe(true);
    expect(() => {
      (s1.grants as unknown as { push: (x: unknown) => void }).push({});
    }).toThrow();
  });
});

// G2(a): a grant may NOT be minted over sensitive roots. Enforced at creation
// (the authoritative choke point) so bypassing the native picker still fails.
describe("GrantStore — sensitive-root policy (G2)", () => {
  let tmp: string;

  beforeEach(() => {
    idCounter = 0;
    tmp = mkdtempSync(join(tmpdir(), "cap-grants-sens-"));
  });
  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  function makeStoreWith(homeDir: string, userDataDir: string = tmp) {
    return new GrantStore({
      userDataDir,
      homeDir,
      safeStorage: makeFakeSafeStorage(true),
      uuid: seqUuid,
      clock: () => 1000,
    });
  }

  it("rejects the filesystem root", async () => {
    const store = makeStoreWith("/Users/alice");
    await expect(
      store.create({ root: "/", mode: "read_only", label: "root" }),
    ).rejects.toThrow(/sensitive/u);
  });

  it("rejects the home directory itself", async () => {
    const store = makeStoreWith("/Users/alice");
    await expect(
      store.create({ root: "/Users/alice", mode: "read_only", label: "home" }),
    ).rejects.toThrow(/sensitive/u);
  });

  it("rejects an ancestor of the home directory", async () => {
    const store = makeStoreWith("/Users/alice");
    await expect(
      store.create({ root: "/Users", mode: "read_only", label: "users" }),
    ).rejects.toThrow(/sensitive/u);
  });

  it("rejects the app userData directory (holds the grant store + secrets)", async () => {
    const store = makeStoreWith("/Users/alice", "/Users/alice/AppData/copilot");
    await expect(
      store.create({
        root: "/Users/alice/AppData/copilot",
        mode: "read_only",
        label: "ud",
      }),
    ).rejects.toThrow(/sensitive/u);
  });

  it("rejects a credential directory anywhere in the path (.ssh / .aws)", async () => {
    const store = makeStoreWith("/Users/alice");
    await expect(
      store.create({
        root: "/Users/alice/.ssh",
        mode: "read_only",
        label: "ssh",
      }),
    ).rejects.toThrow(/sensitive/u);
    await expect(
      store.create({
        root: "/Users/alice/.aws/cache",
        mode: "read_only",
        label: "aws",
      }),
    ).rejects.toThrow(/sensitive/u);
  });

  it("allows a normal project folder under home", async () => {
    const store = makeStoreWith("/Users/alice");
    const grant = await store.create({
      root: "/Users/alice/projects/atlas",
      mode: "read_write",
      label: "atlas",
    });
    expect(grant.status).toBe("active");
    expect(grant.root).toBe("/Users/alice/projects/atlas");
  });

  it("never echoes the offending path in the rejection message", async () => {
    const store = makeStoreWith("/Users/secret-person");
    await store
      .create({
        root: "/Users/secret-person/.ssh",
        mode: "read_only",
        label: "x",
      })
      .then(
        () => {
          throw new Error("expected a rejection");
        },
        (err: unknown) => {
          expect((err as Error).message).not.toContain("secret-person");
        },
      );
  });
});
