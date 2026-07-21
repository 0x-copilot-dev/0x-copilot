export {
  TIER3_SCHEME,
  type SaaSRendererAdapter,
  type SaaSRendererAdapterMetadata,
  type SaaSRendererAdapterOrigin,
} from "./SaaSRendererAdapter";
export {
  clearRegistry,
  createSurfaceRegistry,
  globalSurfaceRegistry,
  markBroken,
  registerAdapter,
  registerSurface,
  resolveAdapter,
  resolveSurface,
  unregisterAdapter,
  type SurfaceRegistry,
} from "./SurfaceRegistry";
export {
  SurfaceRegistryProvider,
  useSurfaceRegistry,
  type SurfaceRegistryProviderProps,
} from "./SurfaceRegistryContext";
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
