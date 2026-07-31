// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CHANNELS, type WindowBridge } from "@0x-copilot/chat-transport";

import { CAPABILITY_CHANNELS } from "../main/capabilities/channels";
import { WINDOW_CHROME_STATE_CHANNEL } from "../main/window-channels";
// The runtime bridge accepts any string channel (it validates internally);
// this local contract types `invoke` with `string`, so capability channels —
// which are intentionally NOT part of chat-transport's ChannelName union —
// can be exercised without casts.
import type { WindowBridge as LocalWindowBridge } from "./window-bridge-types";

type ElectronListener = (event: unknown, payload: unknown) => void;

const electron = vi.hoisted(() => {
  const listeners = new Map<string, Set<ElectronListener>>();
  const exposed: { bridge?: unknown } = {};

  return {
    exposed,
    listeners,
    contextBridge: {
      exposeInMainWorld: vi.fn((_name: string, bridge: unknown) => {
        exposed.bridge = bridge;
      }),
    },
    ipcRenderer: {
      invoke: vi.fn(() => Promise.resolve(null)),
      on: vi.fn((channel: string, listener: ElectronListener) => {
        const channelListeners = listeners.get(channel) ?? new Set();
        channelListeners.add(listener);
        listeners.set(channel, channelListeners);
      }),
      removeListener: vi.fn((channel: string, listener: ElectronListener) => {
        listeners.get(channel)?.delete(listener);
      }),
    },
  };
});

vi.mock("electron", () => ({
  contextBridge: electron.contextBridge,
  ipcRenderer: electron.ipcRenderer,
}));

function emit(channel: string, payload: unknown): void {
  for (const listener of [...(electron.listeners.get(channel) ?? [])]) {
    listener({}, payload);
  }
}

describe("preload bridge stateful IPC", () => {
  let bridge: WindowBridge;

  beforeEach(async () => {
    vi.resetModules();
    electron.listeners.clear();
    electron.exposed.bridge = undefined;
    electron.contextBridge.exposeInMainWorld.mockClear();
    electron.ipcRenderer.invoke.mockClear();
    electron.ipcRenderer.on.mockClear();
    electron.ipcRenderer.removeListener.mockClear();

    await import("./bridge");
    bridge = electron.exposed.bridge as WindowBridge;
  });

  it("replays boot status that arrived before the renderer subscribed", () => {
    const ready = { phase: "ready", message: "Ready", percent: 100 };
    emit(CHANNELS.bootStatus, ready);

    const handler = vi.fn();
    bridge.ipc.on(CHANNELS.bootStatus, handler);

    expect(handler).toHaveBeenCalledOnce();
    expect(handler).toHaveBeenCalledWith(ready);
  });

  it("delivers live stateful status updates after subscription", () => {
    const handler = vi.fn();
    bridge.ipc.on(CHANNELS.updateStatus, handler);

    const downloaded = { kind: "downloaded", version: "0.2.0" };
    emit(CHANNELS.updateStatus, downloaded);

    expect(handler).toHaveBeenCalledOnce();
    expect(handler).toHaveBeenCalledWith(downloaded);
  });

  it("stops stateful delivery after unsubscribe", () => {
    const handler = vi.fn();
    const unsubscribe = bridge.ipc.on(CHANNELS.bootStatus, handler);

    unsubscribe();
    emit(CHANNELS.bootStatus, {
      phase: "health",
      message: "Checking services",
      percent: 80,
    });

    expect(handler).not.toHaveBeenCalled();
  });

  it("does not replay transient events sent before subscription", () => {
    const event = { subscriptionId: "sub-1", kind: "open" };
    emit(CHANNELS.streamEvent, event);

    const handler = vi.fn();
    bridge.ipc.on(CHANNELS.streamEvent, handler);
    expect(handler).not.toHaveBeenCalled();

    emit(CHANNELS.streamEvent, event);
    expect(handler).toHaveBeenCalledOnce();
    expect(handler).toHaveBeenCalledWith(event);
  });
});

describe("preload bridge capability-channel allowlist", () => {
  let bridge: LocalWindowBridge;

  beforeEach(async () => {
    vi.resetModules();
    electron.listeners.clear();
    electron.exposed.bridge = undefined;
    electron.contextBridge.exposeInMainWorld.mockClear();
    electron.ipcRenderer.invoke.mockClear();
    electron.ipcRenderer.on.mockClear();
    electron.ipcRenderer.removeListener.mockClear();

    await import("./bridge");
    bridge = electron.exposed.bridge as LocalWindowBridge;
  });

  it("forwards the capability channels through ipcRenderer.invoke", async () => {
    await bridge.ipc.invoke(CAPABILITY_CHANNELS.requestFolderGrant, {
      mode: "read_only",
    });
    await bridge.ipc.invoke(CAPABILITY_CHANNELS.listGrants, {});
    await bridge.ipc.invoke(CAPABILITY_CHANNELS.revokeGrant, {
      grantId: "x",
    });
    await bridge.ipc.invoke(CAPABILITY_CHANNELS.decideWorkspaceApproval, {
      snapshot: {
        runId: "run_c3_001",
        stageId: "stage_c3_001",
        revision: 7,
        proposalDigest: "a".repeat(64),
        targetDigest: "b".repeat(64),
      },
      decision: "approve",
    });
    expect(electron.ipcRenderer.invoke).toHaveBeenCalledWith(
      CAPABILITY_CHANNELS.requestFolderGrant,
      { mode: "read_only" },
    );
    expect(electron.ipcRenderer.invoke).toHaveBeenCalledWith(
      CAPABILITY_CHANNELS.decideWorkspaceApproval,
      {
        snapshot: {
          runId: "run_c3_001",
          stageId: "stage_c3_001",
          revision: 7,
          proposalDigest: "a".repeat(64),
          targetDigest: "b".repeat(64),
        },
        decision: "approve",
      },
    );
    expect(electron.ipcRenderer.invoke).toHaveBeenCalledTimes(4);
  });

  it("still rejects a channel that is in neither allowlist", async () => {
    await expect(
      bridge.ipc.invoke("capability.read-file", { grantId: "x" }),
    ).rejects.toThrow(/not in allowlist/u);
    await expect(
      bridge.ipc.invoke("capability.workspace-commit", {
        permit: "wcp_not_renderer_visible",
      }),
    ).rejects.toThrow(/not in allowlist/u);
    expect(electron.ipcRenderer.invoke).not.toHaveBeenCalled();
  });
});

describe("preload bridge window chrome", () => {
  let bridge: LocalWindowBridge;

  beforeEach(async () => {
    vi.resetModules();
    electron.listeners.clear();
    electron.exposed.bridge = undefined;
    electron.contextBridge.exposeInMainWorld.mockClear();
    electron.ipcRenderer.invoke.mockClear();
    electron.ipcRenderer.on.mockClear();
    electron.ipcRenderer.removeListener.mockClear();

    await import("./bridge");
    bridge = electron.exposed.bridge as LocalWindowBridge;
  });

  it("replays the native window snapshot and streams later fullscreen changes", () => {
    emit(WINDOW_CHROME_STATE_CHANNEL, {
      isFullScreen: false,
      hasNativeTrafficLights: true,
    });

    const handler = vi.fn();
    const unsubscribe = bridge.windowChrome?.subscribe(handler);
    expect(handler).toHaveBeenLastCalledWith({
      isFullScreen: false,
      hasNativeTrafficLights: true,
    });

    emit(WINDOW_CHROME_STATE_CHANNEL, {
      isFullScreen: true,
      hasNativeTrafficLights: true,
    });
    expect(handler).toHaveBeenLastCalledWith({
      isFullScreen: true,
      hasNativeTrafficLights: true,
    });

    unsubscribe?.();
    emit(WINDOW_CHROME_STATE_CHANNEL, {
      isFullScreen: false,
      hasNativeTrafficLights: true,
    });
    expect(handler).toHaveBeenCalledTimes(2);
  });

  it("drops malformed native window snapshots", () => {
    const handler = vi.fn();
    bridge.windowChrome?.subscribe(handler);
    emit(WINDOW_CHROME_STATE_CHANNEL, { isFullScreen: "yes" });
    expect(handler).not.toHaveBeenCalled();
  });
});
