import type { WindowChromeState } from "../main/window-channels";

// Shape exposed on window.bridge via contextBridge.exposeInMainWorld.
// Phase 1-A ships a stub that throws on invoke; Phase 1-C populates the
// channel allowlist and wires real IPC. The interface itself is the
// contract — Agent 1-C's renderer-side IpcTransport consumes only this.
export interface WindowBridge {
  readonly ipc: {
    invoke<T = unknown>(channel: string, payload: unknown): Promise<T>;
    on(channel: string, handler: (payload: unknown) => void): () => void;
  };
  /**
   * Desktop-native window state. Optional so isolated renderer tests and
   * non-Electron component harnesses can supply the narrower IPC-only bridge.
   */
  readonly windowChrome?: {
    subscribe(handler: (state: WindowChromeState) => void): () => void;
  };
}

declare global {
  interface Window {
    readonly bridge: WindowBridge;
  }
}

export {};
