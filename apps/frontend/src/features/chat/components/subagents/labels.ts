// Re-export shim for the subagent row + card label helpers.
//
// These pure pause-reason / duration formatters now live in
// @0x-copilot/chat-surface (PR-1.5). Existing import sites keep resolving
// them from here.

export {
  formatSubagentDuration,
  pauseAriaLabel,
  pauseFullLabel,
  pauseJumpLabel,
  pauseShortLabel,
} from "@0x-copilot/chat-surface";
