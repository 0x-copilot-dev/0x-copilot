export {
  TcInlineDiff,
  type InlineDiffState,
  type TcInlineDiffProps,
} from "./TcInlineDiff";
export {
  TcSurfaceMount,
  type TcSurfaceMountProps,
  type PendingDiffHandle,
} from "./TcSurfaceMount";

// === Phase 2-B thread-canvas ===
export {
  ThreadCanvas,
  clampRailWidth,
  DEFAULT_RAIL_WIDTH,
  MIN_RAIL_WIDTH,
  MAX_RAIL_WIDTH,
  type ThreadCanvasProps,
  type ThreadMode,
} from "./ThreadCanvas";
export { TcTabs, type TcTabsProps, type TcTab } from "./TcTabs";
// === end Phase 2-B ===

// === Phase 1 P1-B2 event projector hook ===
export {
  useEventProjector,
  type ActivityConsumer,
  type ChatConsumer,
  type EventProjection,
  type SurfaceConsumer,
  type SwimlanesConsumer,
  type TimelineConsumer,
} from "./useEventProjector";
// === end Phase 1 P1-B2 ===

// === PRD-04 (genui) surface-tab selector ===
// Pure selector over the single canonical run stream — surface-tab strip data
// for the Run cockpit. No second subscription / projector (FR-3.3).
export { projectSurfaceTabs, type SurfaceTab } from "./eventProjector";
// === end PRD-04 (genui) ===

// === PRD-B1 (Generative Surfaces v2) client ledger fold ===
// Pure PEER of `projectSurfaceTabs` over the SAME `session.events` array — folds
// the v2 Work Ledger (`surface.created`/`view.derived`) into named tabs. Its
// `toParitySnapshot` byte-matches PRD-A3's Python SurfaceStore fold.
export {
  projectLedger,
  tabUriForSurface,
  surfaceIdForTabUri,
  ledgerTabsAsSurfaceTabs,
  toParitySnapshot,
  type LedgerProjection,
  type LedgerSurface,
  type LedgerSurfaceKind,
  type LedgerSurfaceSource,
  type LedgerSurfaceView,
  type LedgerViewTier,
} from "./ledgerProjection";
// === end PRD-B1 ===

// === Surfaces v2 — PRD-B2 provenance + honest states ===
// Pure PEERS of `projectLedger` over the SAME `session.events` array, plus the
// footer / skeleton / raw-fallback / status-strip chrome they feed. All mount
// strictly inside B1's v2 canvas subtree (flag-off byte-identical).
export {
  projectProvenance,
  resolveSurfaceOpenIn,
  formatLatency,
  formatAccessClass,
  type SurfaceProvenance,
  type SurfaceOpenIn,
  type SurfaceAccessClass,
  type SurfaceViewTier,
} from "./provenance";
export { projectStatusLine, type StatusStripLine } from "./statusLine";
export { resolveDotPath, isSafeHttpUrl } from "./dotPath";
export {
  TcProvenanceFooter,
  type TcProvenanceFooterProps,
} from "./TcProvenanceFooter";
export {
  TcSurfaceSkeleton,
  type TcSurfaceSkeletonProps,
} from "./TcSurfaceSkeleton";
export { TcSurfaceFrame, type TcSurfaceFrameProps } from "./TcSurfaceFrame";
export { TcStatusStrip, type TcStatusStripProps } from "./TcStatusStrip";
// === end PRD-B2 ===

// === Phase 2-C swimlanes ===
export {
  TcSwimlanes,
  type Playhead,
  type TcSwimlanesProps,
} from "./TcSwimlanes";
// === end Phase 2-C ===

// === Phase 2-D tc-chat ===
export { TcChat, type TcChatProps, type TcChatApproval } from "./TcChat";
export {
  SwimlaneScrubProvider,
  useSwimlaneScrub,
  type SwimlaneScrubState,
} from "./SwimlaneScrubContext";
// === end Phase 2-D ===

// === Phase 2-E inline-diff state-machine ===
export {
  nextInlineDiffState,
  useInlineDiffReducer,
  InvalidInlineDiffTransitionError,
  type InlineDiffEvent,
} from "./TcInlineDiff";
export {
  inlineDiffFixtures as __dev__inlineDiffFixtures,
  type InlineDiffFixture as __dev__InlineDiffFixture,
} from "./TcInlineDiff.fixtures";
// === end Phase 2-E ===
