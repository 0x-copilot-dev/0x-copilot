// @vitest-environment node
//
// "Always allow" on a mid-run filesystem approval — the durable half.
//
// The defect this pins is the one a user actually feels: approving an ad-hoc
// folder was one-shot, so the same folder asked again on the next run. Making it
// durable means writing a REAL grant into the same encrypted store the "attach a
// folder" flow writes to, so it shows up as an ordinary pill and revokes through
// the ordinary path.
//
// What these tests are really guarding is the SCOPE. The card names one folder;
// routing that click into a free picker (which is what happened before `path`
// was honoured) let the answer land on the parent, and the resulting pill would
// claim access to a tree nobody agreed to. So: exactly the named folder, never
// its parent, never writable, and never a location main would refuse.

import {
  mkdirSync,
  mkdtempSync,
  realpathSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SafeStorageLike } from "../auth/secret-storage";
import { CapabilityBroker } from "./broker";
import { FolderPicker, type ShowOpenDialogResult } from "./folder-picker";
import { GrantStore } from "./grant-store";
import { UnavailableNativeWorkspaceAuthority } from "./native-workspace-authority";
import { RequestFolderGrantParamsSchema } from "./schemas";
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

/**
 * A service over a REAL temporary filesystem, so realpath / isDirectory are the
 * genuine node implementations rather than stubs that agree with the test.
 */
function makeService(userDataDir: string) {
  const showOpenDialog = vi.fn<() => Promise<ShowOpenDialogResult>>(
    async () => ({
      canceled: true,
      filePaths: [],
    }),
  );
  const store = new GrantStore({
    userDataDir,
    safeStorage: fakeSafeStorage(),
  });
  const picker = new FolderPicker({ showOpenDialog });
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
    showOpenDialog,
  };
}

describe("always-allow mints a grant on the named folder", () => {
  let tmp: string;
  let userData: string;

  beforeEach(() => {
    // Canonical from the start: main stores the REALPATH, and on macOS
    // `/var/folders/...` resolves to `/private/var/folders/...`.
    tmp = realpathSync(mkdtempSync(join(tmpdir(), "named-grant-")));
    userData = join(tmp, "userData");
    mkdirSync(userData);
  });
  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  it("attaches the folder that was named, with no dialog", async () => {
    // FAILS before the fix: `path` was not on the channel schema, so the only
    // way to mint was the picker — a different folder, chosen again.
    const reports = join(tmp, "reports");
    mkdirSync(reports);
    const { service, store, showOpenDialog } = makeService(userData);

    const view = await service.requestFolderGrant({
      mode: "read_only",
      path: reports,
    });

    expect(showOpenDialog).not.toHaveBeenCalled();
    expect(view).not.toBeNull();
    const stored = await store.listActive();
    expect(stored.map((g) => g.root)).toEqual([reports]);
  });

  it("is an ORDINARY grant — it lists and revokes through the existing path", async () => {
    // The requirement is not "some durable state exists"; it is that the user
    // sees the same pill and can take it away the same way.
    const reports = join(tmp, "reports");
    mkdirSync(reports);
    const { service } = makeService(userData);

    const view = await service.requestFolderGrant({
      mode: "read_only",
      path: reports,
    });
    const listed = await service.listGrants();
    expect(listed).toHaveLength(1);
    expect(listed[0].grantId).toBe(view!.grantId);
    expect(listed[0].status).toBe("active");

    const revoked = await service.revokeGrant(view!.grantId);
    expect(revoked!.status).toBe("revoked");
    expect((await service.listGrants())[0].status).toBe("revoked");
  });

  it("never leaks the host path back to the renderer", async () => {
    const reports = join(tmp, "reports");
    mkdirSync(reports);
    const { service } = makeService(userData);

    const view = await service.requestFolderGrant({
      mode: "read_only",
      path: reports,
    });

    expect(Object.keys(view!).sort()).toEqual([
      "grantId",
      "label",
      "mode",
      "status",
    ]);
    expect(JSON.stringify(view)).not.toContain(reports);
  });

  it("labels the grant from the folder main resolved, not from the caller", async () => {
    // A caller-supplied label WINS over the basename, so honouring it would let
    // a pill read "Downloads" over a grant on Documents.
    const documents = join(tmp, "Documents");
    mkdirSync(documents);
    const { service } = makeService(userData);

    const view = await service.requestFolderGrant({
      mode: "read_only",
      path: documents,
      label: "Downloads",
    });

    expect(view!.label).toBe("Documents");
  });
});

describe("a named folder can never widen what a grant covers", () => {
  let tmp: string;
  let userData: string;

  beforeEach(() => {
    // Canonical from the start: main stores the REALPATH, and on macOS
    // `/var/folders/...` resolves to `/private/var/folders/...`.
    tmp = realpathSync(mkdtempSync(join(tmpdir(), "named-grant-")));
    userData = join(tmp, "userData");
    mkdirSync(userData);
  });
  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  it("grants the named folder and NOT its parent", async () => {
    const parent = join(tmp, "clients");
    const named = join(parent, "reports");
    mkdirSync(named, { recursive: true });
    const { service, store } = makeService(userData);

    await service.requestFolderGrant({ mode: "read_only", path: named });

    const roots = (await store.listActive()).map((g) => g.root);
    expect(roots).toEqual([named]);
    expect(roots).not.toContain(parent);
  });

  it("is read-only even when the caller asks for write access", async () => {
    // This lane is reachable only from a filesystem READ approval, and a
    // filesystem interrupt must never authorize a mutation — host writes go
    // through the staged/attested workspace protocol, not through a grant
    // minted off a read card.
    const reports = join(tmp, "reports");
    mkdirSync(reports);
    const { service } = makeService(userData);

    const view = await service.requestFolderGrant({
      mode: "read_write",
      path: reports,
    });

    expect(view!.mode).toBe("read_only");
  });

  it("still refuses the locations main would never grant", async () => {
    // `assertGrantableRoot` is the one choke point, and naming a path must not
    // route around it. The home directory is the cheapest of its refusals to
    // assert without depending on machine layout.
    const { service } = makeService(userData);

    await expect(
      service.requestFolderGrant({ mode: "read_only", path: homedir() }),
    ).rejects.toThrow(/sensitive location/u);
  });

  it("resolves a symlink rather than granting the link itself", async () => {
    // Otherwise the link could be re-pointed at another tree while the grant
    // stays live — the grant would silently follow it.
    const real = join(tmp, "real");
    const link = join(tmp, "link");
    mkdirSync(real);
    symlinkSync(real, link);
    const { service, store } = makeService(userData);

    await service.requestFolderGrant({ mode: "read_only", path: link });

    expect((await store.listActive()).map((g) => g.root)).toEqual([real]);
  });

  it("refuses a path that is a file rather than a folder", async () => {
    const file = join(tmp, "q3.csv");
    writeFileSync(file, "a,b\n");
    const { service, store } = makeService(userData);

    await expect(
      service.requestFolderGrant({ mode: "read_only", path: file }),
    ).rejects.toThrow(/not a folder/u);
    expect(await store.listActive()).toHaveLength(0);
  });

  it("refuses a folder that is not there", async () => {
    const { service, store } = makeService(userData);

    await expect(
      service.requestFolderGrant({
        mode: "read_only",
        path: join(tmp, "gone"),
      }),
    ).rejects.toThrow(/could not be resolved/u);
    expect(await store.listActive()).toHaveLength(0);
  });
});

describe("the channel contract", () => {
  it("accepts an optional path and still rejects anything else", () => {
    expect(
      RequestFolderGrantParamsSchema.parse({
        mode: "read_only",
        path: "/Users/ada/Reports",
      }),
    ).toEqual({ mode: "read_only", path: "/Users/ada/Reports" });
    // Unchanged for the composer's "attach a folder" button.
    expect(RequestFolderGrantParamsSchema.parse({ mode: "read_only" })).toEqual(
      {
        mode: "read_only",
      },
    );
    // `.strict()` still holds — a new field is a deliberate act, not a spread.
    expect(() =>
      RequestFolderGrantParamsSchema.parse({ mode: "read_only", root: "/x" }),
    ).toThrow();
    expect(() =>
      RequestFolderGrantParamsSchema.parse({ mode: "read_only", path: "" }),
    ).toThrow();
  });
});
