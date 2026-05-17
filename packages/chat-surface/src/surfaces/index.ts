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

// === Phase 4-B tier3-generic-diff ===
export {
  GenericStructuredDiff,
  registerGenericStructuredDiff,
  type GenericCurrentState,
  type GenericFieldChange,
  type GenericStructuredDiffPayload,
} from "./GenericStructuredDiff";
// === end Phase 4-B ===
