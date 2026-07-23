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

// === WC-P5a — MCP-OAuth launcher port TYPE (AD-6) ===
export type { McpAuthPort } from "./mcpAuthPort";

// === PRD-C2 — global write-posture chip ===
export { PostureChip, type PostureChipProps } from "./PostureChip";

// === PRD-E2 — cross-run pending-work queue (selector + hook + counter chip) ===
export {
  projectPendingCards,
  type PendingCard,
} from "./pendingCardsProjection";
export { usePendingWork, type UsePendingWorkResult } from "./usePendingWork";
export {
  PendingCounterChip,
  type PendingCounterChipProps,
} from "./PendingCounterChip";

// === WC-P6a — citation projection (in-chat chip resolution, AD-11) ===
export { projectCitations, type CitationProjection } from "./projectCitations";

// === Phase 3 (PR-3.11) run empty/multi-run ===
export {
  RunEmptyState,
  type RunEmptyStateProps,
  type StartRunError,
} from "./RunEmptyState";
export { RunMultiSelect, type RunMultiSelectProps } from "./RunMultiSelect";
