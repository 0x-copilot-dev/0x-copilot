// Re-export shim for the subagent card view-model adapter.
//
// The adapter now lives in @0x-copilot/chat-surface (PR-1.5). It shapes both
// upstream data sources (in-thread `run_subagent` args + workspace-pane
// `SubagentEntry`) into one `SubagentCardViewModel`; the status-normalisation
// and text helpers it needs were reproduced substrate-portably in
// chat-surface (FR-1.17), so the host `chatModel/subagentStatus` +
// `utils/activityDataBuilders` stay host-owned and unchanged. Existing import
// sites keep resolving the builders + types from here.

export {
  subagentCardFromArgs,
  subagentCardFromEntry,
  type SubagentCardStatus,
  type SubagentCardViewModel,
  type SubagentPauseReason,
} from "@0x-copilot/chat-surface";
