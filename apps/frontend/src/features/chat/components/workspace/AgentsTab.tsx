// Re-export shim for the Workspace pane Agents tab.
//
// The tab body now lives in @0x-copilot/chat-surface (PR-1.7) so web and
// desktop render the right rail identically. It has no substrate-specific
// dependencies (it renders the already-hoisted `SubagentCard` and takes the
// host's normalised subagent snapshot + activities + jump callbacks as props),
// so this is a pure re-export; existing import sites keep resolving `AgentsTab`
// from here.

export { AgentsTab, type AgentsTabProps } from "@0x-copilot/chat-surface";
