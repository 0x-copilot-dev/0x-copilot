// Re-export shim for the subagent inner-activity timeline list.
//
// The component now lives in @0x-copilot/chat-surface (PR-1.5) as a shared
// leaf of the subagent card family. Existing import sites keep resolving
// `SubagentActivityList` from here.

export { SubagentActivityList } from "@0x-copilot/chat-surface";
