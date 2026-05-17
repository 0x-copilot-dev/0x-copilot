// Host-managed surface lifecycle. Not consumed in MVP — introduced here so
// Phase 6 (tier-2 dynamic adapter loading) and any future rich-canvas
// surfaces have a stable shape to target. Implementations live in
// apps/desktop (main process) or in chat-surface for the web substrate.
// Pure surface lifecycle: mount/unmount/pause/snapshot/events. Does NOT
// carry any I/O — surfaces talk to MCP via the host's TcSurfaceMount, not
// via this port (D28).
export interface SurfaceHost {
  mountSurface(args: {
    readonly id: string;
    readonly uri: string;
    readonly rect: DOMRect;
  }): Promise<SurfaceHandle>;
  unmountSurface(id: string): Promise<void>;
  pauseSurface(id: string): Promise<void>;
  resumeSurface(id: string): Promise<void>;
  snapshotSurface(id: string, t: number): Promise<Blob>;
  onSurfaceEvent(handler: (event: SurfaceEvent) => void): () => void;
}

export interface SurfaceHandle {
  readonly id: string;
}

export interface SurfaceEvent {
  readonly surfaceId: string;
  readonly type: string;
  readonly payload?: unknown;
}
