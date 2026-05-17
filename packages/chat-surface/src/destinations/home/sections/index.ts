// Section-component barrel for the Home destination. P2-B1's
// `HomeDestination` imports from here so each section file stays the
// canonical home for its presentation logic.
//
// TODO(merge): once api-types/src/home.ts ships and `_home-stub.ts` is
// removed, this barrel keeps its shape — only the underlying type imports
// shift.

export { ActivityFeed, type ActivityFeedProps } from "./ActivityFeed";
export { Greeting, type GreetingProps } from "./Greeting";
export { PinnedChats, type PinnedChatsProps } from "./PinnedChats";
