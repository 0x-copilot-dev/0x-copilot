import type { Conversation } from "@0x-copilot/api-types";
import { type ReactElement } from "react";
import { groupConversations } from "../../utils/groupConversations";
import { ConversationRow } from "./ConversationRow";

/**
 * Sidebar list — buckets the input into Today / Yesterday / Earlier
 * (with optional folder sub-groups) via `groupConversations`, then
 * renders one `ConversationRow` per item.
 *
 * Receives `now` so tests / storybook can drive the time bucket
 * deterministically. In ChatScreen we pass `new Date()`.
 *
 * PR 2.2.1 — `liveConversationIds` is a Set so any number of chats can
 * pulse simultaneously (background runs across conversations). The old
 * `switchingDisabled` gate is gone; rows are always clickable because
 * the runtime keeps non-visible streams alive instead of tearing them
 * down on switch.
 */
export function ConversationListGroups({
  conversations,
  now,
  activeConversationId,
  liveConversationIds,
  onSelect,
  onTogglePin,
  onArchive,
  pinnedIds,
}: {
  conversations: readonly Conversation[];
  now: Date;
  activeConversationId: string | null;
  liveConversationIds: ReadonlySet<string>;
  onSelect: (conversationId: string) => void;
  onTogglePin?: (conversationId: string, nextPinned: boolean) => void;
  onArchive?: (conversationId: string) => void;
  pinnedIds?: ReadonlySet<string>;
}): ReactElement {
  const groups = groupConversations(conversations, now, pinnedIds);
  if (groups.length === 0) {
    return (
      <p className="aui-sidebar__note" role="status">
        No threads match.
      </p>
    );
  }
  return (
    <div className="aui-conversation-groups">
      {groups.map((group) => (
        <section key={group.id} className="aui-conversation-group">
          <h3 className="aui-conversation-group__heading">{group.label}</h3>
          <ul className="aui-conversation-group__list">
            {group.conversations.map((conversation) => (
              <li key={conversation.conversation_id}>
                <ConversationRow
                  conversation={conversation}
                  active={conversation.conversation_id === activeConversationId}
                  isLive={liveConversationIds.has(conversation.conversation_id)}
                  onSelect={onSelect}
                  onTogglePin={onTogglePin}
                  onArchive={onArchive}
                />
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
