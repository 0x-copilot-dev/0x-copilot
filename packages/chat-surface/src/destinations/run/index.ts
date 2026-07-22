// Run destination — module barrel.
//
// The Run cockpit lives in `packages/chat-surface/src/destinations/run/` and is
// consumed by `apps/desktop` (and, later, web) through the package root
// (`@0x-copilot/chat-surface`). This barrel is the module's single public
// surface: the composition shell (`RunDestination` + `RunHeader`, PR-3.5) and
// the host hooks (`useRunSession` PR-3.3, `useRunMode` PR-3.4) it builds on.

// === PR-3.5 — cockpit shell ===
export {
  RunDestination,
  buildRunCreateBody,
  type RunDestinationProps,
  type RunStartRequest,
  type RunEmptyComposerCtx,
} from "./RunDestination";
export { RunHeader, type RunHeaderProps } from "./RunHeader";

// === PR-3.6 — tabbed right rail (Chat · Sources · Agents · Approvals) ===
export {
  RunWorkspaceRail,
  type RunWorkspaceRailProps,
  type RunRailTabId,
} from "./RunWorkspaceRail";

// === PR-3.3 — live run session host hook ===
export {
  useRunSession,
  type RunSession,
  type RunSessionStatus,
  type RunListItem,
  type UseRunSessionOptions,
} from "./useRunSession";

// === PR-3.4 — Studio/Focus mode owner + ⌘M ===
export {
  useRunMode,
  readRunMode,
  writeRunMode,
  runModeKey,
  DEFAULT_RUN_MODE,
  type RunMode,
  type UseRunModeOptions,
  type UseRunModeResult,
} from "./useRunMode";

// === PR-3.10 — approval projection (in-chat card + rail queue) ===
export {
  projectApprovals,
  overlayApprovalDecisions,
  toApprovalsQueue,
  type RunApproval,
  type RunApprovalDecision,
  type RunApprovalKind,
  type ApprovalProjection,
} from "./approvalProjection";

// === Phase 3 (PR-3.11) run empty/multi-run ===
export {
  RunEmptyState,
  type RunEmptyStateProps,
  type StartRunError,
} from "./RunEmptyState";
export { RunMultiSelect, type RunMultiSelectProps } from "./RunMultiSelect";
