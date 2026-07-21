export {
  TIER3_SCHEME,
  type SaaSRendererAdapter,
  type SaaSRendererAdapterMetadata,
  type SaaSRendererAdapterOrigin,
} from "./SaaSRendererAdapter";
export {
  clearRegistry,
  markBroken,
  registerAdapter,
  registerSurface,
  resolveAdapter,
  resolveSurface,
  unregisterAdapter,
} from "./SurfaceRegistry";
export type { PendingDiff, SurfaceRendererProps } from "./types";

// === PRD-10 tier-2 production worker ===
export {
  createTier2WorkerFactory,
  executeAdapterRender,
  TIER2_WORKER_SOURCE,
  TIER2_WORKER_DS_COMPONENT_NAMES,
  type Tier2WorkerFactory,
} from "./tier2Worker";
// === end PRD-10 ===

// === Phase 4-B tier3-generic-diff ===
export {
  GenericStructuredDiff,
  registerGenericStructuredDiff,
  type GenericCurrentState,
  type GenericFieldChange,
  type GenericStructuredDiffPayload,
} from "./GenericStructuredDiff";
// === end Phase 4-B ===
