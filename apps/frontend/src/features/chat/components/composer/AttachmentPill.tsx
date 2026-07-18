// Re-export shim for the composer / user-message attachment chip.
//
// The component now lives in @0x-copilot/chat-surface (PR-1.3) so web and
// desktop render the attachment pill identically. It is a pure
// presentational chip driven by props (attachment + optional onRemove) with
// no substrate-specific dependency, so this is a pure re-export rather than a
// host adapter; existing import sites (`UserMessage`, the composer) keep
// resolving `AttachmentPill` from here.

export { AttachmentPill } from "@0x-copilot/chat-surface";
