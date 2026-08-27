// @vitest-environment node
import { mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { SafeStorageLike } from "../auth/secret-storage";
import { registerIpcHandlers } from "../ipc/handlers";
import { CAPABILITY_BROKER_PROTOCOL, createCapabilityService } from ".";
import { CAPABILITY_CHANNELS } from "./channels";
import {
  DESKTOP_FILESYSTEM_FLAG,
  isDesktopFilesystemEnabled,
  resolveDesktopFilesystemGate,
} from "./feature-gate";

describe("resolveDesktopFilesystemGate (G4)", () => {
  it("is ON when the flag is unset — the default", () => {
    const gate = resolveDesktopFilesystemGate({});
    expect(gate.enabled).toBe(true);
    expect(gate.source).toBe("default");
    // The reason has to say what enabling did NOT do, because that is the
    // sentence an operator reads at boot.
    expect(gate.reason).toContain("nothing is readable until");
  });

  it("takes the default for an empty / whitespace value (no opt-out was expressed)", () => {
    for (const raw of ["", "   ", "\t"]) {
      const gate = resolveDesktopFilesystemGate({
        [DESKTOP_FILESYSTEM_FLAG]: raw,
      });
      expect(gate.enabled).toBe(true);
      expect(gate.source).toBe("default");
    }
  });

  it("honours an explicit opt-out (case / whitespace tolerant) and names it", () => {
    for (const raw of [
      "0",
      "false",
      "FALSE",
      "off",
      "no",
      " disabled ",
      "Off",
    ]) {
      const gate = resolveDesktopFilesystemGate({
        [DESKTOP_FILESYSTEM_FLAG]: raw,
      });
      expect(gate.enabled).toBe(false);
      expect(gate.source).toBe("explicit-off");
      expect(gate.reason).toContain(DESKTOP_FILESYSTEM_FLAG);
      expect(gate.reason).toContain(raw.trim());
    }
  });

  it("is ON for explicit truthy values, reported as a decision not a default", () => {
    for (const raw of ["1", "true", "TRUE", "yes", "on", " enabled ", "On"]) {
      const gate = resolveDesktopFilesystemGate({
        [DESKTOP_FILESYSTEM_FLAG]: raw,
      });
      expect(gate.enabled).toBe(true);
      expect(gate.source).toBe("explicit-on");
    }
  });

  it("fails closed on a value it cannot read, and SAYS so", () => {
    for (const raw of ["nope", "2", "maybe", "true-ish"]) {
      const gate = resolveDesktopFilesystemGate({
        [DESKTOP_FILESYSTEM_FLAG]: raw,
      });
      // An unreadable setting is not consent to enable — but it is also not a
      // decision, so it is reported rather than swallowed.
      expect(gate.enabled).toBe(false);
      expect(gate.source).toBe("unrecognized");
      expect(gate.reason).toContain("unrecognized value");
    }
  });

  it("ignores unrelated env vars", () => {
    expect(resolveDesktopFilesystemGate({ SOMETHING_ELSE: "0" }).enabled).toBe(
      true,
    );
  });
});

describe("isDesktopFilesystemEnabled (G4)", () => {
  it("agrees with the gate it delegates to", () => {
    expect(isDesktopFilesystemEnabled({})).toBe(true);
    expect(isDesktopFilesystemEnabled({ [DESKTOP_FILESYSTEM_FLAG]: "1" })).toBe(
      true,
    );
    expect(isDesktopFilesystemEnabled({ [DESKTOP_FILESYSTEM_FLAG]: "0" })).toBe(
      false,
    );
    expect(
      isDesktopFilesystemEnabled({ [DESKTOP_FILESYSTEM_FLAG]: "nope" }),
    ).toBe(false);
  });
});

// End-to-end linkage: the gate decides whether the capability IPC channels are
// registered at all. This mirrors main/index.ts, which builds the capability
// dependency ONLY when the gate is on; when off, `capability` is undefined and
// registerIpcHandlers never wires the channels — so renderer calls fail closed.
function fakeIpcMain() {
  const handlers = new Set<string>();
  return {
    handle(channel: string) {
      handlers.add(channel);
    },
    removeHandler(channel: string) {
      handlers.delete(channel);
    },
    has(channel: string) {
      return handlers.has(channel);
    },
  };
}

const fakeCapability = {
  requestFolderGrant: async () => null,
  listGrants: async () => [],
  revokeGrant: async () => null,
  setGrantShellEnabled: async () => null,
};

function registerWithGate(env: Record<string, string | undefined>) {
  const ipcMain = fakeIpcMain();
  const bridge = { closeAll() {} };
  const capability = isDesktopFilesystemEnabled(env)
    ? fakeCapability
    : undefined;
  registerIpcHandlers({
    ipcMain: ipcMain as unknown as Parameters<
      typeof registerIpcHandlers
    >[0]["ipcMain"],
    bridge: bridge as unknown as Parameters<
      typeof registerIpcHandlers
    >[0]["bridge"],
    capability,
  });
  return ipcMain;
}

describe("capability subsystem gate → IPC registration (G4)", () => {
  it("default (flag unset): all four capability channels are registered", () => {
    const ipcMain = registerWithGate({});
    expect(ipcMain.has(CAPABILITY_CHANNELS.requestFolderGrant)).toBe(true);
    expect(ipcMain.has(CAPABILITY_CHANNELS.listGrants)).toBe(true);
    expect(ipcMain.has(CAPABILITY_CHANNELS.revokeGrant)).toBe(true);
    expect(ipcMain.has(CAPABILITY_CHANNELS.setGrantShellEnabled)).toBe(true);
  });

  it("explicit opt-out: capability channels are NOT registered (calls fail closed)", () => {
    const ipcMain = registerWithGate({ [DESKTOP_FILESYSTEM_FLAG]: "0" });
    expect(ipcMain.has(CAPABILITY_CHANNELS.requestFolderGrant)).toBe(false);
    expect(ipcMain.has(CAPABILITY_CHANNELS.listGrants)).toBe(false);
    expect(ipcMain.has(CAPABILITY_CHANNELS.revokeGrant)).toBe(false);
    // PRD-shell-execution §7.3. With the capability subsystem off there are no
    // grants, so there is nothing to enable commands ON — and the one channel
    // that can turn command execution on must not outlive the subsystem that
    // owns the authority list it writes to.
    expect(ipcMain.has(CAPABILITY_CHANNELS.setGrantShellEnabled)).toBe(false);
  });

  it("unreadable flag value: also NOT registered (fail closed, not fail open)", () => {
    const ipcMain = registerWithGate({ [DESKTOP_FILESYSTEM_FLAG]: "maybe" });
    expect(ipcMain.has(CAPABILITY_CHANNELS.requestFolderGrant)).toBe(false);
    expect(ipcMain.has(CAPABILITY_CHANNELS.listGrants)).toBe(false);
    expect(ipcMain.has(CAPABILITY_CHANNELS.revokeGrant)).toBe(false);
    expect(ipcMain.has(CAPABILITY_CHANNELS.setGrantShellEnabled)).toBe(false);
  });
});

// DEFAULT-ON IS NOT DEFAULT-ACCESS — asserted against the real composition, not
// a fake. A freshly booted subsystem (which is now what an untouched install
// gets) has an empty grant store, and this pins what that means: the affordance
// exists, and nothing on the disk is readable through it.
//
// The read attempted below targets a directory that DEMONSTRABLY EXISTS and
// DEMONSTRABLY has a file in it, because "empty" and "refused" are the two
// answers this whole subsystem exists to keep apart. A 403 is the correct answer;
// a 200 with `entries: []` would be the original defect.
function safeStorage(): SafeStorageLike {
  return {
    isEncryptionAvailable: () => true,
    encryptString: (value) => Buffer.from(value, "utf8"),
    decryptString: (value) => value.toString("utf8"),
  };
}

describe("enabled subsystem with NO grants (G4)", () => {
  let userDataDir: string;
  let realFolder: string;

  beforeEach(() => {
    userDataDir = mkdtempSync(join(tmpdir(), "feature-gate-grants-"));
    realFolder = realpathSync(mkdtempSync(join(tmpdir(), "feature-gate-fs-")));
    writeFileSync(join(realFolder, "receipt.txt"), "not empty\n");
  });

  afterEach(() => {
    rmSync(userDataDir, { recursive: true, force: true });
    rmSync(realFolder, { recursive: true, force: true });
  });

  it("boots the affordance but exposes no readable path", async () => {
    // The gate is on by default, so this is the untouched install's subsystem.
    expect(resolveDesktopFilesystemGate({}).enabled).toBe(true);
    const service = createCapabilityService({
      userDataDir,
      safeStorage: safeStorage(),
      // The picker is never opened here: nobody has granted anything yet, which
      // is exactly the state under test.
      showOpenDialog: async () => ({ canceled: true, filePaths: [] }),
    });
    const broker = await service.startBroker();
    try {
      // Nothing granted…
      expect(await service.listGrants()).toEqual([]);

      const headers = {
        authorization: `Bearer ${service.brokerAuthToken()}`,
        "x-capability-protocol": CAPABILITY_BROKER_PROTOCOL,
        "content-type": "application/json",
      };
      // …and the broker's own grant list agrees (this is what a child process
      // asking "what may I read?" is told).
      const grants = await fetch(`${broker.baseUrl}/v1/grants/list`, {
        method: "POST",
        headers,
        body: "{}",
      });
      expect(grants.status).toBe(200);
      expect(await grants.json()).toEqual({ grants: [] });

      // A list of a real, non-empty folder is REFUSED — not answered empty.
      const list = await fetch(`${broker.baseUrl}/v1/fs/list`, {
        method: "POST",
        headers,
        body: JSON.stringify({ grant_id: "no-such-grant", path: "" }),
      });
      expect(list.status).toBe(403);
      expect(await list.json()).toEqual({ error: "grant_required" });

      // Same for a read, and same for a stat: there is no lane that answers.
      const read = await fetch(`${broker.baseUrl}/v1/fs/read`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          grant_id: "no-such-grant",
          path: "receipt.txt",
        }),
      });
      expect(read.status).toBe(403);
      expect(await read.json()).toEqual({ error: "grant_required" });

      // And a HOST-ABSOLUTE path is not a back door: it is refused for want of
      // a grant like everything else, so the only way that path can matter is
      // as the subject of an ask.
      const absolute = await fetch(`${broker.baseUrl}/v1/fs/stat`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          grant_id: "no-such-grant",
          path: join(realFolder, "receipt.txt"),
        }),
      });
      expect(absolute.status).toBe(403);
      expect(await absolute.json()).toEqual({ error: "grant_required" });
    } finally {
      await service.stopBroker();
    }
  });
});
