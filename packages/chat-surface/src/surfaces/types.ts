import type { Transport } from "@enterprise-search/chat-transport";

export interface PendingDiff {
  readonly diffId: string;
  readonly provenance: string;
  readonly title: string;
  readonly description?: string;
  readonly regionAnchorId: string;
}

// Spike-shape: parents can pass an activeDiff directly so variant agents
// have a simple way to drive the renderer from outside. Production
// renderers (Phase 4) subscribe to the transport's event stream and
// derive activeDiff internally.
export interface SurfaceRendererProps {
  readonly uri: string;
  readonly transport: Transport;
  readonly activeDiff?: PendingDiff | null;
  readonly onApproveDiff?: (diffId: string) => void;
  readonly onRejectDiff?: (diffId: string) => void;
}
