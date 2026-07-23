// Phase 4 (PR-4.2) — Chats archive destination component.
export {
  ChatsArchive,
  type ChatsArchiveProps,
  CHATS_SECTION_ORDER,
  CHATS_LEAD_COPY,
  type ChatsSectionKey,
} from "./ChatsArchive";

// PRD-09 D1 — the transport-backed controller both hosts bind to.
export {
  useChatsArchive,
  type ChatsArchiveController,
} from "./useChatsArchive";
