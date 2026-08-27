// @vitest-environment node
import {
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SafeStorageLike } from "../auth/secret-storage";
import { GrantStore } from "./grant-store";
import { FORBIDDEN_ROOT_MESSAGES } from "./path-validation";

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

// The home directory these tests run AS. Declared rather than inherited from
// `os.homedir()`: the grant policy is relative to the signed-in account (a
// folder under a SIBLING of home is another user's, and refused), so a store
// that inherits the developer's real home classifies `/Users/x/...` one way on
// a Mac and another way on a Linux runner. Every store below states its home.
const TEST_HOME = "/Users/x";

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
      homeDir: TEST_HOME,
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

  it("normalizes only safe root-relative authority prefixes", async () => {
    const store = makeStore();
    const grant = await store.create({
      root: "/Users/x/projects/atlas",
      mode: "read_write",
      label: "atlas",
      allowedPathPrefixes: ["/docs/", "docs", "src/generated"],
    });
    expect(grant.allowedPathPrefixes).toEqual(["docs", "src/generated"]);

    await expect(
      store.create({
        root: "/Users/x/projects/other",
        mode: "read_write",
        label: "other",
        allowedPathPrefixes: ["../outside"],
      }),
    ).rejects.toThrow("grant path prefix is invalid");
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
      homeDir: TEST_HOME,
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

  it("re-attaching the same folder supersedes rather than accumulates", async () => {
    // Two live grants over one tree are indistinguishable to every surface
    // that lists them, so "stop sharing" on one leaves the folder readable
    // through the other — a dismissed pill that does not remove access.
    const store = makeStore();
    const root = "/Users/x/projects/atlas";
    const first = await store.create({
      root,
      mode: "read_only",
      label: "atlas",
    });
    const second = await store.create({
      root,
      mode: "read_write",
      label: "atlas",
    });

    const active = await store.listActive();
    expect(active).toHaveLength(1);
    expect(active[0].grantId).toBe(second.grantId);
    expect(active[0].mode).toBe("read_write");

    // Retired, not erased: the trail keeps both rows.
    const all = await store.list();
    expect(all.map((g) => [g.grantId, g.status])).toEqual([
      [first.grantId, "revoked"],
      [second.grantId, "active"],
    ]);

    // ...and revoking the one remaining pill really does end the sharing.
    await store.revoke(second.grantId);
    expect(await store.listActive()).toHaveLength(0);
  });

  it("a different folder is still ADDED, never swapped", async () => {
    const store = makeStore();
    await store.create({ root: "/data/a", mode: "read_only", label: "a" });
    await store.create({ root: "/data/b", mode: "read_only", label: "b" });
    await store.create({ root: "/data/c", mode: "read_only", label: "c" });

    expect((await store.listActive()).map((g) => g.root)).toEqual([
      "/data/a",
      "/data/b",
      "/data/c",
    ]);
  });

  it("an expired grant reports as revoked everywhere, not just in active views", async () => {
    // `listActive`/`snapshotActive` already dropped it, but `list` reported the
    // stored literal — so the renderer (which filters on `status`) kept showing
    // a pill for a folder whose every read now answers `grant_required`.
    let now = 1000;
    const store = new GrantStore({
      userDataDir: tmp,
      homeDir: TEST_HOME,
      safeStorage: makeFakeSafeStorage(true),
      uuid: seqUuid,
      clock: () => now,
      grantTtlMs: 500,
    });
    const grant = await store.create({
      root: "/data/reports",
      mode: "read_only",
      label: "reports",
    });
    expect((await store.list())[0].status).toBe("active");

    now = 1_501;

    const all = await store.list();
    expect(all).toHaveLength(1);
    expect(all[0].status).toBe("revoked");
    expect(all[0].expiresAt).toBe(1_500); // expiry is still distinguishable
    expect((await store.get(grant.grantId))?.status).toBe("revoked");
    expect(await store.listActive()).toHaveLength(0);
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
    ).rejects.toThrow(FORBIDDEN_ROOT_MESSAGES.filesystem_root);
  });

  it("rejects the home directory itself", async () => {
    const store = makeStoreWith("/Users/alice");
    await expect(
      store.create({ root: "/Users/alice", mode: "read_only", label: "home" }),
    ).rejects.toThrow(FORBIDDEN_ROOT_MESSAGES.home_directory);
  });

  it("rejects an ancestor of the home directory", async () => {
    const store = makeStoreWith("/Users/alice");
    await expect(
      store.create({ root: "/Users", mode: "read_only", label: "users" }),
    ).rejects.toThrow(FORBIDDEN_ROOT_MESSAGES.home_directory);
  });

  it("rejects the app userData directory (holds the grant store + secrets)", async () => {
    const store = makeStoreWith("/Users/alice", "/Users/alice/AppData/copilot");
    await expect(
      store.create({
        root: "/Users/alice/AppData/copilot",
        mode: "read_only",
        label: "ud",
      }),
    ).rejects.toThrow(FORBIDDEN_ROOT_MESSAGES.user_data_directory);
  });

  it("rejects a credential directory anywhere in the path (.ssh / .aws)", async () => {
    const store = makeStoreWith("/Users/alice");
    await expect(
      store.create({
        root: "/Users/alice/.ssh",
        mode: "read_only",
        label: "ssh",
      }),
    ).rejects.toThrow(FORBIDDEN_ROOT_MESSAGES.sensitive_directory);
    await expect(
      store.create({
        root: "/Users/alice/.aws/cache",
        mode: "read_only",
        label: "aws",
      }),
    ).rejects.toThrow(FORBIDDEN_ROOT_MESSAGES.sensitive_directory);
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

// ---------------------------------------------------------------------------
// Per-workspace SHELL EXECUTION enablement (PRD-shell-execution §7.3).
//
// This block is about ONE boolean, and it is long because the boolean decides
// whether an agent may run code on someone's machine. The four properties it
// pins are the four ways the flag could be wrong:
//
//   1. A workspace with no flag REFUSES. Absence is the common case — every
//      grant minted before the field existed lacks it — so "off" has to be what
//      happens when the value is missing, not only when it is explicitly false.
//   2. Enabling is an EXPLICIT ACT. There is exactly one way in, it is not the
//      attach flow, and nothing about creating or re-creating a grant reaches it.
//   3. The flag is PER-WORKSPACE and does not leak across grants.
//   4. A malformed record FAILS CLOSED — a store we cannot decode authorizes
//      nothing, rather than decoding to whatever JS finds truthy.
// ---------------------------------------------------------------------------
describe("GrantStore — per-workspace shell enablement (§7.3)", () => {
  let tmp: string;

  beforeEach(() => {
    idCounter = 0;
    tmp = mkdtempSync(join(tmpdir(), "cap-grants-shell-"));
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  function makeStore(clock: () => number = () => 1000, grantTtlMs?: number) {
    return new GrantStore({
      userDataDir: tmp,
      homeDir: TEST_HOME,
      safeStorage: makeFakeSafeStorage(true),
      uuid: seqUuid,
      clock,
      grantTtlMs,
    });
  }

  /**
   * Write a grant row straight to disk, bypassing `create`, so the decode seam
   * can be tested against shapes `create` would never mint — including the
   * legacy row that has no `shellEnabled` key at all. Plaintext because the
   * fake cipher is not the thing under test here.
   */
  function seedRawStore(rows: readonly Record<string, unknown>[]): GrantStore {
    mkdirSync(join(tmp, "capabilities"), { recursive: true, mode: 0o700 });
    writeFileSync(
      join(tmp, "capabilities", "grants.bin"),
      `ATLASCAPv1:plaintext:${JSON.stringify({ version: 1, grants: rows })}`,
    );
    return new GrantStore({
      userDataDir: tmp,
      homeDir: TEST_HOME,
      safeStorage: makeFakeSafeStorage(false),
      allowPlaintextFallback: true,
      uuid: seqUuid,
      clock: () => 1000,
    });
  }

  function legacyRow(
    overrides: Record<string, unknown> = {},
  ): Record<string, unknown> {
    return {
      grantId: "00000000-0000-4000-8000-000000009001",
      root: "/Users/x/projects/legacy",
      mode: "read_write",
      label: "legacy",
      status: "active",
      createdAt: 1,
      updatedAt: 1,
      ...overrides,
    };
  }

  // --- 1. Absent means off -------------------------------------------------

  it("a freshly created grant cannot run commands", async () => {
    const store = makeStore();
    const grant = await store.create({
      root: "/Users/x/projects/atlas",
      mode: "read_write",
      label: "atlas",
    });
    expect(grant.shellEnabled).toBe(false);
  });

  it("a grant persisted BEFORE the field existed decodes as off, not undefined", async () => {
    // The common case, and the one an `undefined`-tolerant reader gets wrong.
    // `shellEnabled` is REQUIRED on `Grant`, so this asserts the fold happens at
    // the decode seam — the row has no such key at all.
    const store = seedRawStore([legacyRow()]);
    const [grant] = await store.list();
    expect(grant.shellEnabled).toBe(false);
    expect(Object.prototype.hasOwnProperty.call(grant, "shellEnabled")).toBe(
      true,
    );
  });

  it("an explicit false on disk is off, and an explicit true is honoured", async () => {
    // The `true` half exists so the `false`/absent halves above cannot pass by
    // the flag being unreadable in every direction.
    const off = seedRawStore([legacyRow({ shellEnabled: false })]);
    expect((await off.list())[0].shellEnabled).toBe(false);

    rmSync(join(tmp, "capabilities"), { recursive: true, force: true });
    const on = seedRawStore([legacyRow({ shellEnabled: true })]);
    expect((await on.list())[0].shellEnabled).toBe(true);
  });

  // --- 2. Enabling is an explicit act -------------------------------------

  it("setShellEnabled is the ONLY way in — create ignores a smuggled flag", async () => {
    // `CreateGrantInput` has no `shellEnabled`, so this cannot be written
    // without a cast; the cast is the point. An attach flow that "helpfully"
    // forwarded the field would compile against a looser type one refactor from
    // now, and this asserts the store still mints `false`.
    const store = makeStore();
    const grant = await store.create({
      root: "/Users/x/projects/atlas",
      mode: "read_write",
      label: "atlas",
      shellEnabled: true,
    } as unknown as Parameters<GrantStore["create"]>[0]);
    expect(grant.shellEnabled).toBe(false);
    expect((await store.get(grant.grantId))?.shellEnabled).toBe(false);
  });

  it("setShellEnabled(true) turns it on and the decision survives a reopen", async () => {
    const store = makeStore();
    const grant = await store.create({
      root: "/Users/x/projects/atlas",
      mode: "read_write",
      label: "atlas",
    });
    const updated = await store.setShellEnabled(grant.grantId, true);
    expect(updated?.shellEnabled).toBe(true);
    expect(updated?.grantId).toBe(grant.grantId); // NOT a new grant

    const reopened = new GrantStore({
      userDataDir: tmp,
      safeStorage: makeFakeSafeStorage(true),
    });
    expect((await reopened.get(grant.grantId))?.shellEnabled).toBe(true);
  });

  it("setShellEnabled(false) turns it back off", async () => {
    const store = makeStore();
    const grant = await store.create({
      root: "/Users/x/projects/atlas",
      mode: "read_write",
      label: "atlas",
    });
    await store.setShellEnabled(grant.grantId, true);
    const off = await store.setShellEnabled(grant.grantId, false);
    expect(off?.shellEnabled).toBe(false);
  });

  it("returns null for an id the store has never seen", async () => {
    const store = makeStore();
    expect(
      await store.setShellEnabled("00000000-0000-4000-8000-00000000dead", true),
    ).toBeNull();
  });

  it("re-attaching the same folder does NOT carry command authority forward", async () => {
    // `#supersedeSameRoot` retires the previous grant when a root is re-picked.
    // A fresh grant is a fresh consent: the obvious "carry the setting forward"
    // convenience would re-grant command authority as a side effect of
    // re-picking a folder in a dialog about SHARING one.
    const store = makeStore();
    const root = "/Users/x/projects/atlas";
    const first = await store.create({ root, mode: "read_write", label: "a" });
    await store.setShellEnabled(first.grantId, true);

    const second = await store.create({ root, mode: "read_write", label: "a" });
    expect(second.grantId).not.toBe(first.grantId);
    expect(second.shellEnabled).toBe(false);
    // And the superseded grant is gone as authority, not lingering enabled.
    expect((await store.get(first.grantId))?.status).toBe("revoked");
  });

  it("enabling is refused on a revoked grant, but disabling always lands", async () => {
    // Asymmetric on purpose: a control that REMOVES authority must never be
    // blocked by the state of the record. A control that GRANTS it must be.
    const store = makeStore();
    const grant = await store.create({
      root: "/Users/x/projects/atlas",
      mode: "read_write",
      label: "atlas",
    });
    await store.setShellEnabled(grant.grantId, true);
    await store.revoke(grant.grantId);

    const refused = await store.setShellEnabled(grant.grantId, false);
    expect(refused?.shellEnabled).toBe(false);

    const reEnabled = await store.setShellEnabled(grant.grantId, true);
    // Reported as the record stands — NOT as a success. A revoked workspace
    // must not be sitting there command-capable for whatever re-activates it.
    expect(reEnabled?.shellEnabled).toBe(false);
    expect(reEnabled?.status).toBe("revoked");
  });

  it("enabling is refused on an EXPIRED grant", async () => {
    // Expiry is the failure mode `status === "active"` alone would miss: the
    // stored literal still says active, and only `isLive` knows better.
    let now = 1000;
    const store = makeStore(() => now, 500);
    const grant = await store.create({
      root: "/Users/x/projects/atlas",
      mode: "read_write",
      label: "atlas",
    });
    now = 1_501;
    const result = await store.setShellEnabled(grant.grantId, true);
    expect(result?.shellEnabled).toBe(false);
    expect(result?.status).toBe("revoked");
  });

  it("is idempotent — re-asserting the current value writes nothing", async () => {
    let now = 1000;
    const store = makeStore(() => now);
    const grant = await store.create({
      root: "/Users/x/projects/atlas",
      mode: "read_write",
      label: "atlas",
    });
    now = 2000;
    const noop = await store.setShellEnabled(grant.grantId, false);
    expect(noop?.updatedAt).toBe(1000); // not bumped
    const changed = await store.setShellEnabled(grant.grantId, true);
    expect(changed?.updatedAt).toBe(2000);
  });

  // --- 3. Per-workspace, no leakage ---------------------------------------

  it("enabling one workspace leaves every other workspace off", async () => {
    const store = makeStore();
    const atlas = await store.create({
      root: "/Users/x/projects/atlas",
      mode: "read_write",
      label: "atlas",
    });
    const notes = await store.create({
      root: "/Users/x/projects/notes",
      mode: "read_write",
      label: "notes",
    });
    const docs = await store.create({
      root: "/Users/x/Documents",
      mode: "read_only",
      label: "Documents",
    });

    await store.setShellEnabled(atlas.grantId, true);

    const byId = new Map(
      (await store.list()).map((g) => [g.grantId, g.shellEnabled]),
    );
    expect(byId.get(atlas.grantId)).toBe(true);
    expect(byId.get(notes.grantId)).toBe(false);
    expect(byId.get(docs.grantId)).toBe(false);

    // And the same seen through the snapshot the runtime actually reads.
    const snapshot = await store.snapshotActive();
    expect(
      snapshot.grants.filter((g) => g.shellEnabled).map((g) => g.grantId),
    ).toEqual([atlas.grantId]);
  });

  it("does not widen mode — a read-only folder stays read-only", async () => {
    // Command execution is a different authority, not more file access.
    const store = makeStore();
    const grant = await store.create({
      root: "/Users/x/Documents",
      mode: "read_only",
      label: "Documents",
    });
    const updated = await store.setShellEnabled(grant.grantId, true);
    expect(updated?.mode).toBe("read_only");
    expect(updated?.shellEnabled).toBe(true);
  });

  // --- 4. A malformed record fails closed ---------------------------------

  it.each([
    ['the string "true"', "true"],
    ["the number 1", 1],
    ["an object", {}],
    ["an array", []],
    ["null", null],
  ])("refuses to decode a store whose flag is %s", async (_label, value) => {
    // Every one of these is TRUTHY in JS (except null, which is the shape a
    // sloppy `?? false` would silently accept), so admitting them and coercing
    // later is exactly how a tampered authority file becomes command authority.
    // The throw is the fail-closed behaviour: an undecodable store yields no
    // grants, therefore no command capability, rather than a best guess.
    const store = seedRawStore([legacyRow({ shellEnabled: value })]);
    await expect(store.list()).rejects.toThrow(/invalid fields/u);
  });

  it("one malformed row poisons the whole store, not just its own grant", async () => {
    // Deliberate. A partially-decoded authority list is a list whose contents
    // depend on which rows happened to parse, and silently dropping the
    // unreadable one is how the store's answer stops matching the user's
    // decisions without anything reporting it.
    const store = seedRawStore([
      legacyRow({ shellEnabled: false }),
      legacyRow({
        grantId: "00000000-0000-4000-8000-000000009002",
        shellEnabled: "yes",
      }),
    ]);
    await expect(store.list()).rejects.toThrow(/invalid fields/u);
  });
});
