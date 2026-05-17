export {
  TcInlineDiff,
  type InlineDiffState,
  type TcInlineDiffProps,
} from "./TcInlineDiff";
export { TcSurfaceMount, type TcSurfaceMountProps } from "./TcSurfaceMount";

// === Phase 2-B thread-canvas ===
export { ThreadCanvas, type ThreadCanvasProps } from "./ThreadCanvas";
export { TcTabs, type TcTabsProps, type TcTab } from "./TcTabs";
// === end Phase 2-B ===

// === Phase 2-C swimlanes ===
export {
  TcSwimlanes,
  type Playhead,
  type TcSwimlanesProps,
} from "./TcSwimlanes";
// === end Phase 2-C ===

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
