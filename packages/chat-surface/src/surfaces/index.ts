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
