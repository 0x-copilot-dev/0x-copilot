// Shape exposed on window.bridge via contextBridge.exposeInMainWorld.
// Phase 1-A ships a stub that throws on invoke; Phase 1-C populates the
// channel allowlist and wires real IPC. The interface itself is the
// contract — Agent 1-C's renderer-side IpcTransport consumes only this.
export interface WindowBridge {
  readonly ipc: {
    invoke<T = unknown>(channel: string, payload: unknown): Promise<T>;
    on(channel: string, handler: (payload: unknown) => void): () => void;
  };
}

declare global {
  interface Window {
    readonly bridge: WindowBridge;
  }
}

export {};
