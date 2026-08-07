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
// Editable surface, connector half. `ConnectorSurfaceEditorActions` is the ONLY
// thing a host wires to make a connector-read surface editable — a
// `{disabled, surfaceId, saveEdits}` object it builds around its own Transport.
// Editability is never on the wire and never on a `SurfaceSpec`; a surface the
// host did not open renders read-only.
export {
  attachConnectorEditor,
  CONNECTOR_EDITOR_FIELD,
  createConnectorSurfaceEditor,
  surfaceWriteBackPath,
  type ConnectorSurfaceEditorActions,
  type ConnectorSurfaceEditorConfig,
  type ConnectorWriteBackResult,
} from "./connectorWriteBack";
export {
  isSurfaceHue,
  resolveSurfaceHue,
  SURFACE_HUES,
  surfaceHueForUri,
  type SurfaceHue,
} from "./surfaceHue";

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
