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
export {
  ConnectorConsentCard,
  type ConnectorConsentCardProps,
  type ConnectorConsentState,
} from "./ConnectorConsentCard";
export {
  accessLabel,
  parseApprovalPresentation,
  parseConnectorTrust,
  EMPTY_CONNECTOR_TRUST,
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
