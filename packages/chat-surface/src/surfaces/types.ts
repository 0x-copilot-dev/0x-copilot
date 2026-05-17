import type { Transport } from "@enterprise-search/chat-transport";

export interface PendingDiff {
  readonly diffId: string;
  readonly provenance: string;
  readonly title: string;
  readonly description?: string;
  readonly regionAnchorId: string;
}

/**
 * @deprecated Use {@link import('./SaaSRendererAdapter').SaaSRendererAdapter}.
 * The legacy spike-prep shape mixes transport and approval into the
 * renderer; PRD D28 mandates pure render only. Removed in Phase 4-a.
 */
export interface SurfaceRendererProps {
  readonly uri: string;
  readonly transport: Transport;
  readonly activeDiff?: PendingDiff | null;
  readonly onApproveDiff?: (diffId: string) => void;
  readonly onRejectDiff?: (diffId: string) => void;
}
