import type { ChannelName } from "./rpc-protocol";

// Shape the renderer expects on globalThis.bridge. Constructed by the
// preload script (Agent 1-A's apps/desktop/preload/bridge.ts) via
// contextBridge.exposeInMainWorld. Declared here so:
//   1. IpcTransport can accept a typed `bridge` constructor argument
//      without ambient typing.
//   2. Preload has a single canonical shape to satisfy.
// We do NOT declare a global `window.bridge` — IpcTransport always takes
// the bridge by injection (substrate touchpoint via argument, not ambient
// access). Apps that want to read it off globalThis do so at their own
// margin (apps/desktop/renderer/bootstrap.tsx, owned by Agent 1-A).
export interface WindowBridge {
  readonly ipc: {
    invoke<T = unknown>(channel: ChannelName, payload?: unknown): Promise<T>;
    on(channel: ChannelName, handler: (payload: unknown) => void): () => void;
  };
}
