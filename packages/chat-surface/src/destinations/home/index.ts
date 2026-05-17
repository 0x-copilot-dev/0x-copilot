// Branded IDs (ConversationId / RunId / SkillId) live in
// @enterprise-search/api-types — chat-surface's top-level index.ts
// re-exports them from the canonical declaration site, so consumers
// pull them from there instead of via this destination module.
export {
  HomeDestination,
  type FavoriteTool,
  type HomePayload,
  type PinnedChat,
  type RecentRun,
  type RecentRunStatus,
} from "./HomeDestination";
