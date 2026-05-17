export {
  TcInlineDiff,
  type InlineDiffState,
  type TcInlineDiffProps,
} from "./TcInlineDiff";
export { TcSurfaceMount, type TcSurfaceMountProps } from "./TcSurfaceMount";

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
