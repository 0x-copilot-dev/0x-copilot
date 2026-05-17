import { contextBridge, ipcRenderer, type IpcRendererEvent } from "electron";

import { isAllowedChannel } from "@enterprise-search/chat-transport";

import type { WindowBridge } from "./window-bridge-types";

const bridge: WindowBridge = {
  ipc: {
    invoke<T = unknown>(channel: string, payload: unknown): Promise<T> {
      if (!isAllowedChannel(channel)) {
        return Promise.reject(
          new Error(`bridge.ipc.invoke: channel "${channel}" not in allowlist`),
        );
      }
      return ipcRenderer.invoke(channel, payload) as Promise<T>;
    },
    on(channel: string, handler: (payload: unknown) => void): () => void {
      if (!isAllowedChannel(channel)) {
        throw new Error(`bridge.ipc.on: channel "${channel}" not in allowlist`);
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
