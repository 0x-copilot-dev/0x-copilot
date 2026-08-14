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
export {
  projectWorkspaceStageLifecycle,
  type WorkspaceStageArtifactFallback,
  type WorkspaceStageReview,
  type WorkspaceStageReviewProjection,
} from "./workspaceStageLifecycle";

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
// `WRITE_GATE_APPROVAL_PREFIX` is the SSOT for the parked-write id shape. It is
// hoisted because two consumers depend on agreeing about it exactly: the
// projection (whether an `always` on this card is a scope the `/decision` POST
// carries) and `TcChat` (which card to draw).
export {
  projectApprovals,
  overlayApprovalDecisions,
  toApprovalsQueue,
  WRITE_GATE_APPROVAL_PREFIX,
  type RunApproval,
  type RunApprovalDecision,
  type RunApprovalKind,
  type ApprovalProjection,
} from "./approvalProjection";

// === WC-P5a — MCP-OAuth launcher port TYPE (AD-6) ===
export type { McpAuthBeginOptions, McpAuthPort } from "./mcpAuthPort";
export {
  useConnectorConsentStates,
  type ConnectorConsentStateController,
  type ConnectorConsentStates,
} from "./useConnectorConsentStates";

// === Workspace folder grants — the mid-run folder ask's state machine ===
// Its sibling: `requestGrant` resolves, so this hook settles the card itself and
// needs no host `markGranted`. Resuming the run stays host-owned (`onGranted`).
export {
  useWorkspaceGrantCardStates,
  type WorkspaceGrantCardController,
  type WorkspaceGrantCardHandlers,
  type WorkspaceGrantCardStates,
} from "./useWorkspaceGrantCardStates";

// === PRD-C2 — global write-posture chip ===
export { PostureChip, type PostureChipProps } from "./PostureChip";

// === PRD-E2 — cross-run pending-work queue (selector + hook + counter chip) ===
export {
  projectPendingCards,
  type PendingCard,
} from "./pendingCardsProjection";
export { usePendingWork, type UsePendingWorkResult } from "./usePendingWork";
export {
  projectPendingWorkV2,
  pendingWorkCardV2Key,
  pendingWorkStatusLabelV2,
  pendingWorkSubjectLabelV2,
  type PendingWorkCardV2,
} from "./pendingWorkV2Projection";
export {
  usePendingWorkV2,
  type UsePendingWorkV2Result,
} from "./usePendingWorkV2";
export {
  PendingCounterChip,
  type PendingCounterChipProps,
} from "./PendingCounterChip";

// === WC-P6a — citation projection (in-chat chip resolution, AD-11) ===
export { projectCitations, type CitationProjection } from "./projectCitations";

// === Context-compaction dividers (`compression_note`) ===
// The pure selector only. The DIVIDER itself is `TcCompactionDivider` in
// `thread-canvas/`, mounted through `TcChat`'s `compactionNotices` prop — the
// same shape every other transcript family takes.
export {
  projectCompactionNotices,
  type CompactionNoticeEntry,
} from "./compactionProjection";

// === Phase 3 (PR-3.11) run empty state ===
// `RunMultiSelect` used to ship alongside this: the multi-run selector rail.
// It was removed outright (not just unmounted) — the cockpit binds one run.
export {
  RunEmptyState,
  type RunEmptyStateProps,
  type StartRunError,
} from "./RunEmptyState";
