// Re-export shim for the Workspace pane Skills tab.
//
// The tab body now lives in @0x-copilot/chat-surface (PR-1.7). It renders the
// user's skills (from the host `useSkills` hook) and takes `onPick` /
// `onOpenSettings` callbacks as props, so it has no substrate-specific
// dependency and this is a pure re-export; existing import sites keep resolving
// `SkillsTab` from here.

export { SkillsTab, type SkillsTabProps } from "@0x-copilot/chat-surface";
