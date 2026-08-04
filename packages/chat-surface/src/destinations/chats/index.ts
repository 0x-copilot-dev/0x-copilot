// Phase 4 (PR-4.2) — Chats archive destination component.
export {
  ChatsArchive,
  type ChatsArchiveProps,
  CHATS_SECTION_ORDER,
  CHATS_LEAD_COPY,
  type ChatsSectionKey,
} from "./ChatsArchive";

// PRD-09 D1 — the transport-backed controller both hosts bind to.
// `ChatsArchiveOptions` is its project-scope argument: a host that scopes the
// list to one project needs the type by name to hold the option object it
// passes, so it belongs on the boundary alongside the hook itself.
export {
  useChatsArchive,
  type ChatsArchiveController,
  type ChatsArchiveOptions,
} from "./useChatsArchive";
