import { contextBridge, ipcRenderer, type IpcRendererEvent } from "electron";

import { CHANNELS, isAllowedChannel } from "@0x-copilot/chat-transport";

// Capability channels (AC5) and connector channels (AC9) are app-local, not
// part of the chat-transport package, so they extend the allowlist here. Both
// `channels.ts` modules are dependency-free constants — safe to bundle into the
// preload sandbox.
import { isCapabilityChannel } from "../main/capabilities/channels";
import { isConnectorChannel } from "../main/connectors/channels";

import type { WindowBridge } from "./window-bridge-types";

// The full set of channels the renderer may reach over the bridge: the shared
// transport/auth channels plus the app-local capability + connector channels.
function isBridgeChannel(channel: string): boolean {
  return (
    isAllowedChannel(channel) ||
    isCapabilityChannel(channel) ||
    isConnectorChannel(channel)
  );
}

type IpcHandler = (payload: unknown) => void;

// Boot/update status are state snapshots rather than transient events. Main
// may publish them after `did-finish-load` but before React commits and runs
// its effects, so subscribe eagerly in preload and replay the latest snapshot
// when the renderer eventually attaches.
const statefulChannels = new Set<string>([
  CHANNELS.bootStatus,
  CHANNELS.updateStatus,
]);
const latestStatefulPayloads = new Map<string, unknown>();
const statefulHandlers = new Map<string, Set<IpcHandler>>();

for (const channel of statefulChannels) {
  statefulHandlers.set(channel, new Set());
  ipcRenderer.on(channel, (_event: IpcRendererEvent, payload: unknown) => {
    latestStatefulPayloads.set(channel, payload);
    for (const handler of statefulHandlers.get(channel) ?? []) {
      handler(payload);
    }
  });
}

const bridge: WindowBridge = {
  ipc: {
    invoke<T = unknown>(channel: string, payload: unknown): Promise<T> {
      if (!isBridgeChannel(channel)) {
        return Promise.reject(
          new Error(`bridge.ipc.invoke: channel "${channel}" not in allowlist`),
        );
      }
      return ipcRenderer.invoke(channel, payload) as Promise<T>;
    },
    on(channel: string, handler: (payload: unknown) => void): () => void {
      if (!isBridgeChannel(channel)) {
        throw new Error(`bridge.ipc.on: channel "${channel}" not in allowlist`);
      }
      if (statefulChannels.has(channel)) {
        const handlers = statefulHandlers.get(channel);
        if (handlers === undefined) {
          throw new Error(
            `bridge.ipc.on: stateful channel "${channel}" not initialized`,
          );
        }
        handlers.add(handler);
        if (latestStatefulPayloads.has(channel)) {
          handler(latestStatefulPayloads.get(channel));
        }
        return () => {
          handlers.delete(handler);
        };
      }
      const wrapped = (_event: IpcRendererEvent, payload: unknown): void => {
        handler(payload);
      };
      ipcRenderer.on(channel, wrapped);
      return () => {
        ipcRenderer.removeListener(channel, wrapped);
      };
    },
  },
};

contextBridge.exposeInMainWorld("bridge", bridge);
