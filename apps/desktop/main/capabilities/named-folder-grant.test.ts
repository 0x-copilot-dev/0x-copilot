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
// THIS LANE MOVED A TRUST BOUNDARY. Every other grant in the product is minted
// from a native folder DIALOG: the user navigated to the folder, so the dialog
// itself is the consent and whatever it returns is what they chose. Here there
// is no dialog. The folder is named by the MODEL, printed on a card, and
// attached with one click — so the consent is a STRING SOMEONE READ, and two
// properties have to hold that the dialog used to provide for free:
//
//   1. the folder named must be one that may ever be shared at all
//      (`classifyForbiddenRoot`, exercised as a class in path-validation.test);
//   2. what was displayed must be exactly what is granted — no re-resolution
//      between the render and the grant.
//
// The groups below are the plain "does it work" set, then those two.
// What all of them are really guarding is SCOPE: the card names one folder, and
// routing that click into a free picker (which is what happened before `path`
// was honoured) let the answer land on the parent, whose pill would then claim
// access to a tree nobody agreed to.

import {
  mkdirSync,
  mkdtempSync,
  realpathSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { realpath as realpathAsync } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SafeStorageLike } from "../auth/secret-storage";
import { CapabilityBroker } from "./broker";
import { FolderPicker, type ShowOpenDialogResult } from "./folder-picker";
import { GrantStore } from "./grant-store";
import { UnavailableNativeWorkspaceAuthority } from "./native-workspace-authority";
import { FORBIDDEN_ROOT_MESSAGES } from "./path-validation";
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

interface ServiceOptions {
  /**
   * The account this store belongs to. Defaults to the temporary tree itself,
   * which is what makes that tree grantable at all: the OS temp directory lives
   * under `/private/var` (macOS) or `/tmp` (Linux), and BOTH are system trees
   * the grant policy refuses outright. Declaring the fixture as the home
   * directory uses the same exemption a container account homed at
   * `/var/lib/<app>` relies on in production — not a test-only escape hatch.
   */
  readonly homeDir?: string;
  readonly realpath?: (path: string) => Promise<string>;
}

/**
 * A service over a REAL temporary filesystem, so realpath / lstat are the
 * genuine node implementations rather than stubs that agree with the test.
 *
 * `realpath` is always wrapped in a spy, because "resolved exactly once" is one
 * of the two properties this lane has to keep, and a call COUNT is the only
 * thing that notices a second resolution being introduced later.
 */
function makeService(
  userDataDir: string,
  homeRoot: string,
  options: ServiceOptions = {},
) {
  const showOpenDialog = vi.fn<() => Promise<ShowOpenDialogResult>>(
    async () => ({
      canceled: true,
      filePaths: [],
    }),
  );
  const store = new GrantStore({
    userDataDir,
    homeDir: options.homeDir ?? homeRoot,
    safeStorage: fakeSafeStorage(),
  });
  const realpath = vi.fn<(path: string) => Promise<string>>(
    options.realpath ?? ((path: string) => realpathAsync(path)),
  );
  const picker = new FolderPicker({ showOpenDialog, realpath });
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
    realpath,
  };
}

/** Fresh temp tree per test, canonical from the start. */
function temporaryTree() {
  // macOS resolves `/var/folders/...` to `/private/var/folders/...`; taking the
  // realpath here is what lets every path below be its own canonical form,
  // which is exactly what the named-folder lane requires of its input.
  const tmp = realpathSync(mkdtempSync(join(tmpdir(), "named-grant-")));
  const userData = join(tmp, "userData");
  mkdirSync(userData);
  return { tmp, userData };
}

describe("always-allow mints a grant on the named folder", () => {
  let tmp: string;
  let userData: string;

  beforeEach(() => {
    ({ tmp, userData } = temporaryTree());
  });
  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  it("attaches the folder that was named, with no dialog", async () => {
    // FAILS before the fix: `path` was not on the channel schema, so the only
    // way to mint was the picker — a different folder, chosen again.
    const reports = join(tmp, "reports");
    mkdirSync(reports);
    const { service, store, showOpenDialog } = makeService(userData, tmp);

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
    const { service } = makeService(userData, tmp);

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
    const { service } = makeService(userData, tmp);

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
    const { service } = makeService(userData, tmp);

    const view = await service.requestFolderGrant({
      mode: "read_only",
      path: documents,
      label: "Downloads",
    });

    expect(view!.label).toBe("Documents");
  });
});

// ---------------------------------------------------------------------------
// (2) WHAT IS DISPLAYED IS EXACTLY WHAT IS GRANTED.
//
// The card was rendered from a string, before this process had touched the
// disk; the grant is minted afterwards. Anything allowed to move the answer in
// between — a symlink re-pointed after the render, an ancestor that was a link
// all along, a second realpath pass someone adds later — attaches a folder the
// user never read. So the path is resolved ONCE and the resolution must be the
// identity.
// ---------------------------------------------------------------------------

describe("the folder granted is the folder that was shown", () => {
  let tmp: string;
  let userData: string;

  beforeEach(() => {
    ({ tmp, userData } = temporaryTree());
  });
  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  it("stores the named string byte for byte", async () => {
    const reports = join(tmp, "clients", "acme", "reports");
    mkdirSync(reports, { recursive: true });
    const { service, store } = makeService(userData, tmp);

    await service.requestFolderGrant({ mode: "read_only", path: reports });

    // Not "resolves to the same directory" — the SAME STRING. A grant root is
    // what later reads are authorized against and what a pill is keyed by, so
    // an equivalent-but-different spelling is already a different answer than
    // the one on the card.
    expect((await store.listActive())[0].root).toBe(reports);
  });

  it("resolves the named path EXACTLY ONCE", async () => {
    // The pin against a second resolution being introduced. A second pass is
    // not redundant work — it is a second chance for the answer to change,
    // which is the whole failure this lane has to exclude. FAILS the moment
    // anyone adds one.
    const reports = join(tmp, "reports");
    mkdirSync(reports);
    const { service, realpath } = makeService(userData, tmp);

    await service.requestFolderGrant({ mode: "read_only", path: reports });

    expect(realpath).toHaveBeenCalledTimes(1);
    expect(realpath).toHaveBeenCalledWith(reports);
  });

  it("refuses a folder that resolves somewhere else, and grants nothing", async () => {
    // The "symlink swapped between render and click" case, made deterministic:
    // resolution returns a real, existing, perfectly grantable directory that
    // is simply NOT the one on the card. An implementation that trusted its own
    // resolution would grant `elsewhere`, and the user would never know.
    const shown = join(tmp, "reports");
    const elsewhere = join(tmp, "elsewhere");
    mkdirSync(shown);
    mkdirSync(elsewhere);
    const { service, store } = makeService(userData, tmp, {
      realpath: async () => elsewhere,
    });

    await expect(
      service.requestFolderGrant({ mode: "read_only", path: shown }),
    ).rejects.toThrow(/link to a different location/u);
    expect(await store.listActive()).toHaveLength(0);
  });

  it("refuses a symlink rather than following it to its target", async () => {
    // The same rule with a real link. Granting the TARGET would attach a folder
    // whose name was never displayed; granting the LINK would let it be
    // re-pointed later while the grant stayed live. Neither is on offer.
    const real = join(tmp, "real");
    const link = join(tmp, "link");
    mkdirSync(real);
    symlinkSync(real, link);
    const { service, store } = makeService(userData, tmp);

    await expect(
      service.requestFolderGrant({ mode: "read_only", path: link }),
    ).rejects.toThrow(/not a folder/u);
    expect(await store.listActive()).toHaveLength(0);
  });

  it("refuses a folder reached through a symlinked ancestor", async () => {
    // `lstat` alone cannot see this one — the leaf really is a directory. Only
    // comparing the resolution to what was displayed catches it.
    const real = join(tmp, "real");
    mkdirSync(join(real, "reports"), { recursive: true });
    const link = join(tmp, "link");
    symlinkSync(real, link);
    const { service, store } = makeService(userData, tmp);

    await expect(
      service.requestFolderGrant({
        mode: "read_only",
        path: join(link, "reports"),
      }),
    ).rejects.toThrow(/link to a different location/u);
    expect(await store.listActive()).toHaveLength(0);
  });

  it("refuses a relative path instead of resolving it against this process", async () => {
    // `realpath` would happily anchor it to the app's working directory,
    // producing an absolute path nobody displayed. Rejected before any syscall.
    const { service, store, realpath } = makeService(userData, tmp);

    await expect(
      service.requestFolderGrant({ mode: "read_only", path: "reports" }),
    ).rejects.toThrow(/full path/u);
    expect(realpath).not.toHaveBeenCalled();
    expect(await store.listActive()).toHaveLength(0);
  });

  it("refuses a name whose rendering is not its content", async () => {
    // A right-to-left override reverses what the tail of the path LOOKS like on
    // the card, so the string read and the string granted are different things
    // to the only party whose agreement matters.
    const folder = join(tmp, `re‮stroper‬`);
    mkdirSync(folder);
    const { service, store } = makeService(userData, tmp);

    await expect(
      service.requestFolderGrant({ mode: "read_only", path: folder }),
    ).rejects.toThrow(FORBIDDEN_ROOT_MESSAGES.deceptive_path);
    expect(await store.listActive()).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// (1) WHERE A NAMED FOLDER MAY NEVER BE.
//
// The class-by-class truth lives in path-validation.test.ts, where the policy
// is pure. What is asserted here is that the named-path lane is WIRED to it —
// including that a refused class is refused BEFORE the disk is touched, so the
// sentence the user reads is about the policy rather than about whether the
// folder happened to exist.
// ---------------------------------------------------------------------------

describe("a named folder can never widen what a grant covers", () => {
  let tmp: string;
  let userData: string;

  beforeEach(() => {
    ({ tmp, userData } = temporaryTree());
  });
  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  it("grants the named folder and NOT its parent", async () => {
    const parent = join(tmp, "clients");
    const named = join(parent, "reports");
    mkdirSync(named, { recursive: true });
    const { service, store } = makeService(userData, tmp);

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
    const { service } = makeService(userData, tmp);

    const view = await service.requestFolderGrant({
      mode: "read_write",
      path: reports,
    });

    expect(view!.mode).toBe("read_only");
  });

  it("refuses a path that is a file rather than a folder", async () => {
    const file = join(tmp, "q3.csv");
    writeFileSync(file, "a,b\n");
    const { service, store } = makeService(userData, tmp);

    await expect(
      service.requestFolderGrant({ mode: "read_only", path: file }),
    ).rejects.toThrow(/not a folder/u);
    expect(await store.listActive()).toHaveLength(0);
  });

  it("refuses a folder that is not there", async () => {
    const { service, store } = makeService(userData, tmp);

    await expect(
      service.requestFolderGrant({
        mode: "read_only",
        path: join(tmp, "gone"),
      }),
    ).rejects.toThrow(/could not be found/u);
    expect(await store.listActive()).toHaveLength(0);
  });
});

describe("the locations a named folder may never be", () => {
  let tmp: string;
  let userData: string;
  const HOME = "/Users/alice";

  beforeEach(() => {
    ({ tmp, userData } = temporaryTree());
  });
  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  /** A service belonging to a plausible signed-in account, not to `tmp`. */
  function account() {
    return makeService(userData, tmp, { homeDir: HOME });
  }

  // Each of these is something a model could name in a tool call and a hurried
  // person would approve because the card reads plausibly. Every one of them
  // was GRANTED before the denylist was extended.
  const refusals: ReadonlyArray<readonly [string, string, string]> = [
    ["the home directory itself", HOME, FORBIDDEN_ROOT_MESSAGES.home_directory],
    [
      "an unexpanded home path",
      "~/Downloads",
      FORBIDDEN_ROOT_MESSAGES.home_directory,
    ],
    [
      "another account's folder",
      "/Users/bob/Documents",
      FORBIDDEN_ROOT_MESSAGES.other_user_home,
    ],
    ["system config", "/etc", FORBIDDEN_ROOT_MESSAGES.system_directory],
    ["system state", "/var/log", FORBIDDEN_ROOT_MESSAGES.system_directory],
    [
      "system binaries",
      "/usr/local/bin",
      FORBIDDEN_ROOT_MESSAGES.system_directory,
    ],
    [
      "the macOS firmlink spelling of system config",
      "/private/etc",
      FORBIDDEN_ROOT_MESSAGES.system_directory,
    ],
    [
      "an application bundle",
      "/Applications/Mail.app",
      FORBIDDEN_ROOT_MESSAGES.application_bundle,
    ],
    [
      "the applications folder",
      "/Applications",
      FORBIDDEN_ROOT_MESSAGES.application_bundle,
    ],
    ["a mount parent", "/Volumes", FORBIDDEN_ROOT_MESSAGES.volume_root],
    ["a whole volume", "/Volumes/Backup", FORBIDDEN_ROOT_MESSAGES.volume_root],
    ["a device", "\\\\.\\PhysicalDrive0", FORBIDDEN_ROOT_MESSAGES.device_path],
    [
      "installed apps' saved logins",
      "/Users/alice/Library/Application Support/Slack",
      FORBIDDEN_ROOT_MESSAGES.application_state_directory,
    ],
    [
      "a credential directory",
      "/Users/alice/.ssh",
      FORBIDDEN_ROOT_MESSAGES.sensitive_directory,
    ],
    ["the whole disk", "/", FORBIDDEN_ROOT_MESSAGES.filesystem_root],
  ];

  for (const [what, path, message] of refusals) {
    it(`refuses ${what}, with a sentence the card can show`, async () => {
      const { service, store, realpath } = account();

      await expect(
        service.requestFolderGrant({ mode: "read_only", path }),
      ).rejects.toThrow(message);

      // Nothing minted, and nothing even looked at on disk: a refused class is
      // refused as a class, not discovered by probing.
      expect(await store.listActive()).toHaveLength(0);
      expect(realpath).not.toHaveBeenCalled();
    });
  }

  it("never names the folder in the refusal", async () => {
    const { service } = account();
    await service
      .requestFolderGrant({
        mode: "read_only",
        path: "/Users/bob/Very-Private-Client-Files",
      })
      .then(
        () => {
          throw new Error("expected a refusal");
        },
        (error: unknown) => {
          expect((error as Error).message).not.toContain("Very-Private");
          expect((error as Error).message).not.toContain("bob");
        },
      );
  });

  it("still attaches an ordinary folder in the user's own home", async () => {
    // The refusals above are worth nothing if they have also taken the feature
    // with them.
    const reports = join(tmp, "clients", "acme", "reports");
    mkdirSync(reports, { recursive: true });
    const { service, store } = makeService(userData, tmp);

    const view = await service.requestFolderGrant({
      mode: "read_only",
      path: reports,
    });

    expect(view!.label).toBe(basename(reports));
    expect((await store.listActive()).map((g) => g.root)).toEqual([reports]);
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

  it("rejects a path long enough to have been truncated upstream", () => {
    // The runtime's approval projection cuts a `workspace_grant` path at 1024
    // characters and shows no ellipsis, so at EXACTLY 1024 a path is
    // indistinguishable from the front of a longer one — and the front of a
    // path is an ANCESTOR of the folder that was asked about. The contract
    // stops one short, so nothing that could have been cut ever reaches main.
    const truncatable = `/${"a".repeat(1023)}`;
    expect(truncatable).toHaveLength(1024);
    expect(() =>
      RequestFolderGrantParamsSchema.parse({
        mode: "read_only",
        path: truncatable,
      }),
    ).toThrow();
    expect(() =>
      RequestFolderGrantParamsSchema.parse({
        mode: "read_only",
        path: truncatable.slice(0, 1023),
      }),
    ).not.toThrow();
  });
});
