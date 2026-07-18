// Re-export shim for the collapsed settled-approval receipt.
//
// The presentational component now lives in @0x-copilot/chat-surface
// (PR-1.6). The undo action is a host-driven `onUndo` callback (the POST
// plumbing lives in `ApprovalTool`'s `UndoableReceipt` wrapper), so the
// moved core stays substrate-agnostic and this is a pure re-export.

export {
  ApprovalReceipt,
  type ApprovalReceiptProps,
  type ApprovalReceiptKind,
} from "@0x-copilot/chat-surface";
