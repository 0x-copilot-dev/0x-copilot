export {
  TcInlineDiff,
  type InlineDiffState,
  type TcInlineDiffProps,
} from "./TcInlineDiff";
export { TcSurfaceMount, type TcSurfaceMountProps } from "./TcSurfaceMount";

// === Phase 2-B thread-canvas ===
export {
  ThreadCanvas,
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

// === Phase 2-C swimlanes ===
export {
  TcSwimlanes,
  type Playhead,
  type TcSwimlanesProps,
} from "./TcSwimlanes";
// === end Phase 2-C ===

// === Phase 2-D tc-chat ===
export { TcChat, type TcChatProps } from "./TcChat";
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
