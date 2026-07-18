// Approvals family (PR-1.6). Presentational-only consent surfaces hoisted
// from apps/frontend; the approval routing/wiring (ApprovalTool,
// useApprovalsQueue, ApprovalFocusContext, forward/undo POST plumbing)
// stays host-owned. The host renders these behind its own callbacks.
export { ApprovalCard, type ApprovalCardProps } from "./ApprovalCard";
export {
  ApprovalReceipt,
  type ApprovalReceiptProps,
  type ApprovalReceiptKind,
} from "./ApprovalReceipt";
export { ActivityDetails } from "./ActivityDetails";
export { ActivityParams } from "./ActivityParams";
export { useUndoCountdown, type UndoCountdownState } from "./useUndoCountdown";
export type { ActivityParam } from "./types";
