// Chats destination — archive host (desktop redesign, Phase 4 · PR-4.2).
//
// Recast of the earlier sidebar+thread-canvas placeholder: the Chats
// destination is now a conversation ARCHIVE, not a live thread canvas.
// A row click / Enter reopens the thread in the Run cockpit (reopen →
// Run); "New chat" opens Run on a fresh conversation. There is no inline
// thread canvas here (FR-4.7) — the Run destination (Phase 3) owns that.
//
// This is a thin pure-presentation wrapper around `ChatsArchive`: it
// forwards data + callbacks straight through. The web/desktop host
// binder (PR-4.3) supplies the fetched `archive` and the
// `onReopen`/`onNewChat` handlers that route to `ArtifactRoute.run`.
// Callbacks default to no-ops and `archive` defaults to `null` (loading)
// so the destination can mount before its binder is wired.
//
// The legacy `ChatsSidebar` (which read the `/v1/chats/projects` stub)
// stays in-tree and exported for Run's own thread rail if needed, but is
// no longer mounted by this destination (PRD §5 — retire from top-level).

import type { ReactElement } from "react";

import { ChatsArchive, type ChatsArchiveProps } from "./ChatsArchive";

const noop = (): void => undefined;

export type ChatsDestinationProps = ChatsArchiveProps;

export function ChatsDestination(
  props: Partial<ChatsDestinationProps> = {},
): ReactElement {
  const {
    archive = null,
    onReopen = noop,
    onNewChat = noop,
    onRetry,
    now,
  } = props;

  return (
    <ChatsArchive
      archive={archive}
      onReopen={onReopen}
      onNewChat={onNewChat}
      onRetry={onRetry}
      now={now}
    />
  );
}
