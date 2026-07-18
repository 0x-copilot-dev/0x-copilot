// Re-export shim for the composer file-attachment `accept` allow-list.
//
// The constant now lives in @0x-copilot/chat-surface (PR-1.3) alongside the
// hoisted AssistantComposer shell that consumes it. Kept as a re-export so any
// existing / future host import site resolves `fileAttachmentAccept` from the
// original path.

export { fileAttachmentAccept } from "@0x-copilot/chat-surface";
