// <PinnedChats> — Home's pinned-chats section.
//
// Source: docs/atlas-new-design/destinations/home-prd.md §3.1.3 + §12.3
// + §12.6. Wraps the shared `<DocList>` primitive in slot mode (the
// pinned-chat row has a non-trivial layout — title + ItemLink +
// last-message timestamp + optional unread badge — so we render the row
// chrome ourselves but still get DocList's consistent `<ul>` shell).
//
// SectionResult branches:
//
// - `status === "ok"`: render `<DocList>` of pinned rows.
// - `status === "ok"` with zero rows: per-section empty state.
// - `status === "error"`: render `<EmptyState>` with optional Retry CTA
//   (§12.6 partial-failure pattern).
// - `status === "unavailable"`: suppressed (returns null).
//
// Every click flows through `<ItemLink kind="chat">` — no destination
// component owns navigation; the registry resolves the ref into a route.

import type { CSSProperties, ReactElement } from "react";

import type { SectionResult } from "@enterprise-search/api-types";

import { ItemLink } from "../../../refs/ItemLink";
import { DocList } from "../../../shell/DocList";
import { EmptyState } from "../../../shell/EmptyState";
import { formatRelativeTime } from "../../../util/time";
// TODO(merge): rewire to "@enterprise-search/api-types" once home.ts ships.
import type { HomePinnedChat } from "../_home-stub";

export interface PinnedChatsProps {
  readonly pinned: SectionResult<ReadonlyArray<HomePinnedChat>>;
  /** Pin `now` for tests; defaults to `Date.now()` at render. */
  readonly nowMs?: number;
  /** Invoked when the user clicks the per-section retry CTA. */
  readonly onRetry?: () => void;
}

const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  marginBottom: 12,
};

const titleStyle: CSSProperties = {
  fontSize: "var(--font-size-lg, 16px)",
  fontWeight: 600,
  color: "var(--color-text, #ededee)",
  margin: 0,
};

const rowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  width: "100%",
  minWidth: 0,
};

const rowMainStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
  flex: 1,
  minWidth: 0,
};

const subtitleStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle, #7e7e84)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const timestampStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle, #7e7e84)",
  flexShrink: 0,
};

const unreadBadgeStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  minWidth: 18,
  height: 18,
  padding: "0 6px",
  borderRadius: "var(--radius-full, 999px)",
  backgroundColor: "var(--color-accent, #d97757)",
  color: "var(--color-accent-contrast, #1a0f0a)",
  fontSize: "var(--font-size-2xs, 11px)",
  fontWeight: 600,
  flexShrink: 0,
};

function PinnedRow({
  chat,
  now,
}: {
  chat: HomePinnedChat;
  now: number | undefined;
}): ReactElement {
  return (
    <div
      style={rowStyle}
      data-testid="home-pinned-row"
      data-conversation-id={chat.conversation_id}
    >
      <div style={rowMainStyle}>
        <ItemLink ref={{ kind: "chat", id: chat.conversation_id }} />
        {chat.subtitle !== undefined && chat.subtitle.length > 0 ? (
          <div style={subtitleStyle} data-testid="home-pinned-subtitle">
            {chat.subtitle}
          </div>
        ) : null}
      </div>
      {chat.unread_message_count > 0 ? (
        <span
          style={unreadBadgeStyle}
          aria-label={`${chat.unread_message_count} unread`}
          data-testid="home-pinned-unread"
        >
          {chat.unread_message_count}
        </span>
      ) : null}
      <time
        style={timestampStyle}
        dateTime={chat.last_message_at}
        data-testid="home-pinned-timestamp"
      >
        {formatRelativeTime(chat.last_message_at, now)}
      </time>
    </div>
  );
}

export function PinnedChats({
  pinned,
  nowMs,
  onRetry,
}: PinnedChatsProps): ReactElement | null {
  if (pinned.status === "unavailable") {
    return null;
  }

  if (pinned.status === "error") {
    return (
      <section
        data-testid="home-pinned-chats"
        data-status="error"
        aria-label="Pinned chats"
      >
        <header style={headerStyle}>
          <h2 style={titleStyle}>Pinned chats</h2>
        </header>
        <EmptyState
          title="Couldn't load pinned chats"
          body={
            pinned.error !== undefined && pinned.error.length > 0
              ? pinned.error
              : "Other sections are unaffected. Try again in a moment."
          }
          action={
            onRetry !== undefined
              ? { label: "Retry", onClick: onRetry }
              : undefined
          }
        />
      </section>
    );
  }

  const rows = pinned.data ?? [];
  if (rows.length === 0) {
    return (
      <section
        data-testid="home-pinned-chats"
        data-status="empty"
        aria-label="Pinned chats"
      >
        <header style={headerStyle}>
          <h2 style={titleStyle}>Pinned chats</h2>
        </header>
        <EmptyState
          title="No pinned chats yet"
          body="Pin a conversation to see it here."
        />
      </section>
    );
  }

  return (
    <section
      data-testid="home-pinned-chats"
      data-status="ok"
      aria-label="Pinned chats"
    >
      <header style={headerStyle}>
        <h2 style={titleStyle}>Pinned chats</h2>
      </header>
      <DocList<HomePinnedChat>
        items={rows}
        keyFor={(chat) => chat.conversation_id}
        ariaLabel="Pinned chats"
        renderRow={(chat) => <PinnedRow chat={chat} now={nowMs} />}
      />
    </section>
  );
}
