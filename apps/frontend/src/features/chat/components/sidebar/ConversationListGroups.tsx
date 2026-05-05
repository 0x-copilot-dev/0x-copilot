import type { Conversation } from "@enterprise-search/api-types";
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
 */
export function ConversationListGroups({
  conversations,
  now,
  activeConversationId,
  liveConversationId,
  switchingDisabled,
  onSelect,
}: {
  conversations: readonly Conversation[];
  now: Date;
  activeConversationId: string | null;
  liveConversationId: string | null;
  switchingDisabled: boolean;
  onSelect: (conversationId: string) => void;
}): ReactElement {
  const groups = groupConversations(conversations, now);
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
                  isLive={conversation.conversation_id === liveConversationId}
                  disabled={
                    switchingDisabled &&
                    conversation.conversation_id !== activeConversationId
                  }
                  onSelect={onSelect}
                />
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
