import type { Conversation } from "@enterprise-search/api-types";
import { type ReactElement } from "react";

/**
 * One conversation row in the sidebar (PR 2.2).
 *
 * Pure presentation: title, optional folder + relative timestamp footer,
 * "live pulse" badge when the row owns the active run. Click handler is
 * passed in — the sidebar owns the routing decision.
 *
 * Rendered as a plain `<button>` (not `ThreadListItemPrimitive.Trigger`)
 * so we control the markup; thread-switching is wired by the parent
 * `Sidebar` via the runtime's `threadListAdapter.onSwitchToThread`
 * callback.
 */
export function ConversationRow({
  conversation,
  active,
  isLive,
  disabled,
  onSelect,
}: {
  conversation: Conversation;
  active: boolean;
  isLive: boolean;
  disabled: boolean;
  onSelect: (conversationId: string) => void;
}): ReactElement {
  const title = conversation.title?.trim() || "Untitled chat";
  const time = formatRelativeTime(conversation.updated_at);
  return (
    <button
      type="button"
      className="aui-thread-list-item__trigger aui-conversation-row"
      data-active={active ? "true" : undefined}
      data-live={isLive ? "true" : undefined}
      aria-current={active ? "true" : undefined}
      disabled={disabled}
      title={
        disabled ? "Stop the current response before switching threads" : title
      }
      onClick={() => {
        if (!disabled) {
          onSelect(conversation.conversation_id);
        }
      }}
    >
      {isLive ? (
        <span className="aui-conversation-row__pulse" aria-hidden="true" />
      ) : null}
      <span className="aui-conversation-row__title">{title}</span>
      {isLive ? <span className="sr-only">live</span> : null}
      <span className="aui-conversation-row__meta">
        {conversation.folder ? (
          <span className="aui-conversation-row__folder">
            {conversation.folder}
          </span>
        ) : null}
        <span className="aui-conversation-row__time">{time}</span>
      </span>
    </button>
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
