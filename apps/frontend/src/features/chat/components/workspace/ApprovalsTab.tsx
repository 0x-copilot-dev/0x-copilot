// Re-export shim for the Workspace pane Approvals tab.
//
// The tab body now lives in @0x-copilot/chat-surface (PR-1.7). It is a pure
// projection view over the `ApprovalsQueueProjection` the host `useApprovalsQueue`
// hook produces, plus the host-supplied `onJumpToApproval` focus callback, so
// this is a pure re-export; existing import sites keep resolving `ApprovalsTab`
// from here.

export { ApprovalsTab, type ApprovalsTabProps } from "@0x-copilot/chat-surface";
