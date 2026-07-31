// Approvals family (PR-1.6). Presentational-only consent surfaces hoisted
// from apps/frontend; the approval routing/wiring (ApprovalTool,
// useApprovalsQueue, ApprovalFocusContext, forward/undo POST plumbing)
// stays host-owned. The host renders these behind its own callbacks.
export { ApprovalCard, type ApprovalCardProps } from "./ApprovalCard";
// The design-accurate consent surfaces: one approval frame with three shapes,
// and the four-state connector card. Their CSS ships from this package
// (`approvals.css`) so both hosts get it — the previous card's rules lived only
// in the web app and never reached desktop.
export { ConsentCard, type ConsentCardProps } from "./ConsentCard";
// `ask_a_question` is an interrupt, not an approval: it blocks the run, it can
// outlive the screen you are on, and its answer is an answer — not a consent.
export {
  QuestionCard,
  type QuestionCardProps,
  type QuestionAnswer,
} from "./QuestionCard";
export {
  parseQuestion,
  composeAnswer,
  isAnswerable,
  type QuestionSpec,
  type QuestionOption,
} from "./question";
export {
  ConnectorConsentCard,
  type ConnectorConsentCardProps,
  type ConnectorConsentState,
} from "./ConnectorConsentCard";
// The folder-grant ask. Same `.cc` frame as the connector card because it is the
// same kind of moment (a capability the run does not have yet); the decision
// itself is host-owned — it runs through `WorkspaceGrantPort`, whose native
// dialog is the only thing that can actually hand over a folder.
export {
  WorkspaceGrantCard,
  type WorkspaceGrantCardProps,
  type WorkspaceGrantCardState,
} from "./WorkspaceGrantCard";
export {
  accessLabel,
  grantAccessLabel,
  parseApprovalPresentation,
  parseConnectorTrust,
  parseWorkspaceGrantRequest,
  EMPTY_CONNECTOR_TRUST,
  WORKSPACE_GRANT_PAYLOAD_KEY,
  type WorkspaceGrantRequest,
  type ApprovalLayout,
  type ApprovalPresentation,
  type ApprovalPreview,
  type ApprovalRow,
  type ApprovalRowStatus,
  type ConnectorTrust,
} from "./presentation";
export {
  ApprovalReceipt,
  type ApprovalReceiptProps,
  type ApprovalReceiptKind,
} from "./ApprovalReceipt";
export { ActivityDetails } from "./ActivityDetails";
export { ActivityParams } from "./ActivityParams";
export { useUndoCountdown, type UndoCountdownState } from "./useUndoCountdown";
export type { ActivityParam } from "./types";
