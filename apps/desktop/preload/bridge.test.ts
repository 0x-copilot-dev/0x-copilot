// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CHANNELS, type WindowBridge } from "@0x-copilot/chat-transport";

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
