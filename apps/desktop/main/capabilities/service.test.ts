// @vitest-environment node
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { SafeStorageLike } from "../auth/secret-storage";
import { CapabilityBroker } from "./broker";
import { FolderPicker, type ShowOpenDialogResult } from "./folder-picker";
import { GrantStore } from "./grant-store";
import { CapabilityService } from "./service";

function fakeSafeStorage(): SafeStorageLike {
  return {
    isEncryptionAvailable: () => true,
    encryptString: (p: string) => Buffer.from(`C:${p}`, "utf-8"),
    decryptString: (c: Buffer) => c.toString("utf-8").slice(2),
  };
}

function makeService(
  showOpenDialog: () => Promise<ShowOpenDialogResult>,
  userDataDir: string,
  realpath: (p: string) => Promise<string> = async (p) => p,
) {
  const store = new GrantStore({
    userDataDir,
    safeStorage: fakeSafeStorage(),
  });
  const picker = new FolderPicker({
    showOpenDialog,
    realpath,
    stat: async () => ({ isDirectory: () => true }),
  });
  const broker = new CapabilityBroker({ grants: store });
  return { service: new CapabilityService({ store, picker, broker }), store };
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

  it("revokeGrant returns null for an unknown id", async () => {
    const { service } = makeService(
      async () => ({ canceled: true, filePaths: [] }),
      tmp,
    );
    expect(
      await service.revokeGrant("00000000-0000-4000-8000-000000000000"),
    ).toBeNull();
  });
});
