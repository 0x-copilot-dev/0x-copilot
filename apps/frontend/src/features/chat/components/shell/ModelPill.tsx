// Re-export shim for the topbar model picker.
//
// The component now lives in @0x-copilot/chat-surface (PR-1.2) so web and
// desktop render the model pill identically. It takes its data (models,
// value, callbacks) purely via props and has no substrate-specific
// dependency, so this is a pure re-export rather than a host adapter;
// existing import sites (and the `shell` barrel) keep resolving `ModelPill`
// from here.

export { ModelPill, type ModelPillProps } from "@0x-copilot/chat-surface";
