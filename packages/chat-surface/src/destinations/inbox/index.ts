// InboxItemId lives in @enterprise-search/api-types — see Phase 0.5
// shared-primitives migration. The top-level chat-surface index
// re-exports it from the canonical site.
export {
  InboxDestination,
  type InboxFilter,
  type InboxItem,
  type InboxItemKind,
  type InboxPayload,
} from "./InboxDestination";
