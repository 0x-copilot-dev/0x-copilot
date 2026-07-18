// Re-export shim for the shared subagent card primitive.
//
// The component now lives in @0x-copilot/chat-surface (PR-1.5) so web and
// desktop render multi-agent runs identically. It has no substrate-specific
// dependencies (the host passes normalised view-model + activity data in as
// props), so this is a pure re-export; existing import sites keep resolving
// `SubagentCard` from here.

export { SubagentCard, type SubagentCardProps } from "@0x-copilot/chat-surface";
