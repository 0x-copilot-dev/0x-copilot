import type { Conversation } from "@0x-copilot/api-types";
import { useState, type ReactElement } from "react";
import { isPinned } from "../../utils/groupConversations";

/**
 * One conversation row in the sidebar (PR 2.2 + PR F3 + PR 2.2.1).
 *
 * Single-line layout: status dot + ellipsized title + time. Hover/focus
 * swaps the time for a small ⋯ overflow that exposes pin/unpin and archive.
 * Pin lives on the first-class `conversation.pinned` column (PRD-09 D2),
 * toggled via `POST /v1/agent/conversations/{id}/pin`. Archive flips
 * `archived_at` via the existing `updateConversation` route. Both actions are
 * wired up by the parent (`Sidebar`).
 *
 * PR 2.2.1 dropped the `disabled` prop: the runtime now keeps non-
 * visible conversations' streams running in the background, so there
 * is no longer a state where switching threads has to be blocked.
 */
export function ConversationRow({
  conversation,
  active,
  isLive,
  onSelect,
  onTogglePin,
  onArchive,
}: {
  conversation: Conversation;
  active: boolean;
  isLive: boolean;
  onSelect: (conversationId: string) => void;
  onTogglePin?: (conversationId: string, nextPinned: boolean) => void;
  onArchive?: (conversationId: string) => void;
}): ReactElement {
  const title = conversation.title?.trim() || "Untitled chat";
  const time = formatRelativeTime(conversation.updated_at);
  const pinned = isPinned(conversation);
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div
      className="aui-conversation-row-host"
      data-active={active ? "true" : undefined}
    >
      <button
        type="button"
        className="aui-thread-list-item__trigger aui-conversation-row"
        data-active={active ? "true" : undefined}
        data-live={isLive ? "true" : undefined}
        aria-current={active ? "true" : undefined}
        aria-label={title}
        onClick={() => onSelect(conversation.conversation_id)}
      >
        <span
          className="aui-conversation-row__status"
          data-live={isLive ? "true" : undefined}
          role={isLive ? "status" : undefined}
          aria-label={isLive ? "Live run" : undefined}
          aria-hidden={isLive ? undefined : "true"}
        />
        <span className="aui-conversation-row__title">
          {pinned ? (
            <span className="aui-conversation-row__pin" aria-hidden="true">
              ⚲
            </span>
          ) : null}
          {title}
        </span>
        <span className="aui-conversation-row__time">{time}</span>
      </button>
      {onTogglePin || onArchive ? (
        <div className="aui-conversation-row__menu">
          <button
            type="button"
            className="aui-conversation-row__more"
            aria-label="Conversation actions"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={(event) => {
              event.stopPropagation();
              setMenuOpen((current) => !current);
            }}
          >
            ⋯
          </button>
          {menuOpen ? (
            <div
              className="aui-conversation-row__menu-pop"
              role="menu"
              onMouseLeave={() => setMenuOpen(false)}
            >
              {onTogglePin ? (
                <button
                  type="button"
                  role="menuitem"
                  className="aui-conversation-row__menu-item"
                  onClick={(event) => {
                    event.stopPropagation();
                    setMenuOpen(false);
                    onTogglePin(conversation.conversation_id, !pinned);
                  }}
                >
                  {pinned ? "Unpin" : "Pin to top"}
                </button>
              ) : null}
              {onArchive ? (
                <button
                  type="button"
                  role="menuitem"
                  className="aui-conversation-row__menu-item"
                  onClick={(event) => {
                    event.stopPropagation();
                    setMenuOpen(false);
                    onArchive(conversation.conversation_id);
                  }}
                >
                  Archive
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** Best-effort local-time relative timestamp; no library. */
function formatRelativeTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const now = Date.now();
  const diff = now - date.getTime();
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (diff < minute) {
    return "now";
  }
  if (diff < hour) {
    return `${Math.floor(diff / minute)}m`;
  }
  if (diff < day) {
    return new Intl.DateTimeFormat(undefined, {
      hour: "numeric",
      minute: "2-digit",
    }).format(date);
  }
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
  }).format(date);
}
