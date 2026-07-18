// Re-export shim for the thought-process accordion.
//
// The component now lives in @0x-copilot/chat-surface (PR-1.1) so web and
// desktop render the reasoning disclosure identically. It has no
// substrate-specific dependencies, so this is a pure re-export rather than
// a host adapter; existing import sites keep resolving `ReasoningGroup`
// from here.

export { ReasoningGroup } from "@0x-copilot/chat-surface";
