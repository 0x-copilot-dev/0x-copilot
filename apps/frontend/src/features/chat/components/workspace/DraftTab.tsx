// Re-export shim for the Workspace pane Draft tab.
//
// The tab body now lives in @0x-copilot/chat-surface (PR-1.7). The PATCH /
// SEND / DISCARD network calls stay host-owned — they arrive as the
// `onPatch` / `onSend` / `onDiscard` async callbacks `ChatScreen` wires to the
// facade — so the body itself has no substrate-specific dependency and this is
// a pure re-export; existing import sites keep resolving `DraftTab` here.

export { DraftTab, type DraftTabProps } from "@0x-copilot/chat-surface";
