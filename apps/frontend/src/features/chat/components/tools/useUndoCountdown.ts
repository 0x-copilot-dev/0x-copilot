// Re-export shim for the consent-card undo-window countdown hook.
//
// The hook now lives in @0x-copilot/chat-surface (PR-1.6). On the move its
// tick was rewritten from `window.setInterval` to the bare `setInterval`
// global (FR-1.30) so the package stays substrate-agnostic; behavior (the
// 1000 ms tick) is byte-identical. `ApprovalReceipt` consumes it there;
// this shim keeps the original host import path resolving.

export {
  useUndoCountdown,
  type UndoCountdownState,
} from "@0x-copilot/chat-surface";
