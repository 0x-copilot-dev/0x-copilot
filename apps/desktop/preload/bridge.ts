import { contextBridge } from "electron";

import type { WindowBridge } from "./window-bridge-types";

// Phase 1-A stub. Phase 1-C populates the channel allowlist and replaces
// these throws with real ipcRenderer.invoke / ipcRenderer.on calls. The
// surface (ipc.invoke / ipc.on) is the load-bearing contract Agent 1-C's
// IpcTransport consumes — keeping it stable at the preload boundary
// means swapping bodies at integration time, not re-shaping the bridge.
function notYetWiredError(method: "invoke" | "on"): Error {
  return new Error(
    `bridge.ipc.${method}: not yet wired (Phase 1-C lands the IPC channel allowlist)`,
  );
}

const bridge: WindowBridge = {
  ipc: {
    invoke<T = unknown>(_channel: string, _payload: unknown): Promise<T> {
      throw notYetWiredError("invoke");
    },
    on(_channel: string, _handler: (payload: unknown) => void): () => void {
      throw notYetWiredError("on");
    },
  },
};

contextBridge.exposeInMainWorld("bridge", bridge);
