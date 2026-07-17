import type { Conversation } from "@0x-copilot/api-types";

/**
 * Sidebar search filter (PR 2.2). Pure, case-insensitive over `title` and
 * (optional) `folder`. Empty / whitespace query returns the input
 * unchanged so callers can pass the raw string with no precondition.
 */
export function filterConversations(
  conversations: readonly Conversation[],
  query: string,
): Conversation[] {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) {
    return [...conversations];
  }
  return conversations.filter((conversation) => {
    const title = (conversation.title ?? "").toLocaleLowerCase();
    if (title.includes(needle)) {
      return true;
    }
    const folder = (conversation.folder ?? "").toLocaleLowerCase();
    return folder.length > 0 && folder.includes(needle);
  });
}
