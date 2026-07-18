// @vitest-environment node
import { describe, expect, it } from "vitest";

import { registerIpcHandlers } from "../ipc/handlers";
import { CAPABILITY_CHANNELS } from "./channels";
import {
  DESKTOP_FILESYSTEM_FLAG,
  isDesktopFilesystemEnabled,
} from "./feature-gate";

describe("isDesktopFilesystemEnabled (G4)", () => {
  it("is OFF when the flag is unset (fail closed)", () => {
    expect(isDesktopFilesystemEnabled({})).toBe(false);
  });

  it("is OFF for empty / falsey / unrecognized values", () => {
    for (const raw of ["", "0", "false", "off", "no", "nope", "  "]) {
      expect(
        isDesktopFilesystemEnabled({ [DESKTOP_FILESYSTEM_FLAG]: raw }),
      ).toBe(false);
    }
  });

  it("is ON for explicit truthy values (case / whitespace tolerant)", () => {
    for (const raw of ["1", "true", "TRUE", "yes", "on", " enabled ", "On"]) {
      expect(
        isDesktopFilesystemEnabled({ [DESKTOP_FILESYSTEM_FLAG]: raw }),
      ).toBe(true);
    }
  });

  it("ignores unrelated env vars", () => {
    expect(isDesktopFilesystemEnabled({ SOMETHING_ELSE: "1" })).toBe(false);
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
  it("gated OFF: capability channels are NOT registered (calls fail closed)", () => {
    const ipcMain = registerWithGate({});
    expect(ipcMain.has(CAPABILITY_CHANNELS.requestFolderGrant)).toBe(false);
    expect(ipcMain.has(CAPABILITY_CHANNELS.listGrants)).toBe(false);
    expect(ipcMain.has(CAPABILITY_CHANNELS.revokeGrant)).toBe(false);
  });

  it("gated ON: all three capability channels are registered", () => {
    const ipcMain = registerWithGate({ [DESKTOP_FILESYSTEM_FLAG]: "1" });
    expect(ipcMain.has(CAPABILITY_CHANNELS.requestFolderGrant)).toBe(true);
    expect(ipcMain.has(CAPABILITY_CHANNELS.listGrants)).toBe(true);
    expect(ipcMain.has(CAPABILITY_CHANNELS.revokeGrant)).toBe(true);
  });
});
