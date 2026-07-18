// Re-export shim for the 4-zone approval consent card.
//
// The presentational component now lives in @0x-copilot/chat-surface
// (PR-1.6) so web and desktop render consent identically. It has no
// substrate-specific dependencies — the Approve/Reject/Forward controls are
// supplied by the host as the `actions` prop — so this is a pure re-export
// rather than a host adapter. `ApprovalTool` keeps importing `ApprovalCard`
// from here unchanged.

export { ApprovalCard, type ApprovalCardProps } from "@0x-copilot/chat-surface";
