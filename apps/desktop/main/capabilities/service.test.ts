// @vitest-environment node
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { SafeStorageLike } from "../auth/secret-storage";
import { CapabilityBroker } from "./broker";
import { FolderPicker, type ShowOpenDialogResult } from "./folder-picker";
import { GrantStore } from "./grant-store";
import { UnavailableNativeWorkspaceAuthority } from "./native-workspace-authority";
import { CapabilityService } from "./service";
import {
  InMemoryWorkspaceJournalStore,
  LocalWorkspaceAuthority,
} from "./workspace-authority";

function fakeSafeStorage(): SafeStorageLike {
  return {
    isEncryptionAvailable: () => true,
    encryptString: (p: string) => Buffer.from(`C:${p}`, "utf-8"),
    decryptString: (c: Buffer) => c.toString("utf-8").slice(2),
  };
}

// Stated, not inherited from `os.homedir()`: the grant policy is relative to
// the signed-in account, so a store that borrowed the developer's real home
// would classify `/Users/me/proj` as another user's folder on a Mac and as an
// ordinary one on a Linux runner.
const TEST_HOME = "/Users/me";

function makeService(
  showOpenDialog: () => Promise<ShowOpenDialogResult>,
  userDataDir: string,
  realpath: (p: string) => Promise<string> = async (p) => p,
) {
  const store = new GrantStore({
    userDataDir,
    homeDir: TEST_HOME,
    safeStorage: fakeSafeStorage(),
  });
  const picker = new FolderPicker({
    showOpenDialog,
    realpath,
    stat: async () => ({ isDirectory: () => true }),
  });
  const workspaceAuthority = new LocalWorkspaceAuthority({
    grants: store,
    native: new UnavailableNativeWorkspaceAuthority(),
    journal: new InMemoryWorkspaceJournalStore(),
    attestation: {
      workspaceWriteIsolation: "unavailable",
      nativeWorkspacePrimitives: "unavailable",
    },
    production: true,
    deviceId: "test-device",
  });
  const broker = new CapabilityBroker({ grants: store, workspaceAuthority });
  return {
    service: new CapabilityService({
      store,
      picker,
      broker,
      workspaceAuthority,
    }),
    store,
  };
}

describe("CapabilityService", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = mkdtempSync(join(tmpdir(), "cap-svc-"));
  });
  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  it("requestFolderGrant mints a grant and returns a renderer-safe view (no path)", async () => {
    const { service } = makeService(
      async () => ({ canceled: false, filePaths: ["/Users/me/proj"] }),
      tmp,
      async () => "/Users/me/proj",
    );
    const view = await service.requestFolderGrant({ mode: "read_write" });
    expect(view).not.toBeNull();
    expect(Object.keys(view!).sort()).toEqual([
      "grantId",
      "label",
      "mode",
      // Per-workspace shell enablement (PRD-shell-execution §7.3). It is a
      // boolean, not a path, so the path-free property this assertion guards is
      // unchanged — but the key COUNT is part of the guard, so it is named here
      // rather than allowed to slip in unlisted.
      "shellEnabled",
      "status",
    ]);
    // The host path must NOT be present anywhere in the renderer payload.
    expect(JSON.stringify(view)).not.toContain("/Users/me/proj");
    expect(view!.mode).toBe("read_write");
    expect(view!.label).toBe("proj");
    expect(view!.status).toBe("active");
  });

  it("requestFolderGrant returns null when the user cancels", async () => {
    const { service } = makeService(
      async () => ({ canceled: true, filePaths: [] }),
      tmp,
    );
    expect(await service.requestFolderGrant({ mode: "read_only" })).toBeNull();
  });

  it("sanitizes a renderer-supplied label", async () => {
    const { service } = makeService(
      async () => ({ canceled: false, filePaths: ["/a/b"] }),
      tmp,
      async () => "/a/b",
    );
    const view = await service.requestFolderGrant({
      mode: "read_only",
      label: "my/label   spaced",
    });
    expect(view!.label).toBe("my label spaced");
  });

  it("listGrants + revokeGrant reflect state without leaking paths", async () => {
    const { service } = makeService(
      async () => ({ canceled: false, filePaths: ["/data/reports"] }),
      tmp,
      async () => "/data/reports",
    );
    const created = await service.requestFolderGrant({ mode: "read_only" });
    const list = await service.listGrants();
    expect(list).toHaveLength(1);
    expect(JSON.stringify(list)).not.toContain("/data/reports");

    const revoked = await service.revokeGrant(created!.grantId);
    expect(revoked!.status).toBe("revoked");

    const after = await service.listGrants();
    expect(after[0].status).toBe("revoked");
  });

  it("does not offer the renderer a folder that has expired", async () => {
    // The renderer decides what to show by `status` alone (it has no path and
    // no expiry). An expired grant that still reports "active" is a folder pill
    // for a folder whose every read answers `grant_required`, with nothing on
    // screen to explain it — while `snapshotActive`, the projection reads are
    // actually authorized against, had already dropped it.
    let now = 1_000;
    const store = new GrantStore({
      userDataDir: tmp,
      homeDir: TEST_HOME,
      safeStorage: fakeSafeStorage(),
      clock: () => now,
      grantTtlMs: 500,
    });
    const picker = new FolderPicker({
      showOpenDialog: async () => ({
        canceled: false,
        filePaths: ["/data/reports"],
      }),
      realpath: async () => "/data/reports",
      stat: async () => ({ isDirectory: () => true }),
    });
    const workspaceAuthority = new LocalWorkspaceAuthority({
      grants: store,
      native: new UnavailableNativeWorkspaceAuthority(),
      journal: new InMemoryWorkspaceJournalStore(),
      attestation: {
        workspaceWriteIsolation: "unavailable",
        nativeWorkspacePrimitives: "unavailable",
      },
      production: true,
      deviceId: "test-device",
    });
    const service = new CapabilityService({
      store,
      picker,
      broker: new CapabilityBroker({ grants: store, workspaceAuthority }),
      workspaceAuthority,
    });
    await service.requestFolderGrant({ mode: "read_only" });
    expect((await service.listGrants())[0].status).toBe("active");

    now = 1_501;

    expect((await service.listGrants())[0].status).toBe("revoked");
  });

  it("revokeGrant returns null for an unknown id", async () => {
    const { service } = makeService(
      async () => ({ canceled: true, filePaths: [] }),
      tmp,
    );
    expect(
      await service.revokeGrant("00000000-0000-4000-8000-000000000000"),
    ).toBeNull();
  });

  // PRD-shell-execution §7.3 — the service method the Settings toggle reaches.
  // It exists because `CapabilityService` had NO mutation of an existing grant
  // at all: `requestFolderGrant` / `listGrants` / `revokeGrant` and nothing
  // else, so a toggle had no method to call and the only compiling stand-in
  // (`requestFolderGrant`) mints a new grantId, supersedes the folder's
  // existing authority, and opens a native picker.
  it("setGrantShellEnabled flips the flag on the SAME grant, and only that one", async () => {
    const { service } = makeService(
      async () => ({ canceled: false, filePaths: ["/data/reports"] }),
      tmp,
      async () => "/data/reports",
    );
    const created = await service.requestFolderGrant({ mode: "read_write" });
    expect(created!.shellEnabled).toBe(false);

    const updated = await service.setGrantShellEnabled(created!.grantId, true);
    expect(updated!.grantId).toBe(created!.grantId);
    expect(updated!.shellEnabled).toBe(true);
    // It does not widen file access: commands are a different authority.
    expect(updated!.mode).toBe("read_write");
    // And it still leaks no path.
    expect(JSON.stringify(updated)).not.toContain("/data/reports");

    const off = await service.setGrantShellEnabled(created!.grantId, false);
    expect(off!.shellEnabled).toBe(false);
  });

  it("setGrantShellEnabled returns null for an unknown id", async () => {
    const { service } = makeService(
      async () => ({ canceled: true, filePaths: [] }),
      tmp,
    );
    expect(
      await service.setGrantShellEnabled(
        "00000000-0000-4000-8000-000000000000",
        true,
      ),
    ).toBeNull();
  });
});
