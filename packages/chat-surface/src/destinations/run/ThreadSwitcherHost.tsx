// ThreadSwitcherHost — binds the cockpit's Threads panel to the shared archive.
//
// Source: docs/plan/windowed-mode/PRD-01-thread-switching.md (FR-1.1, NFR-1.1).
//
// This is the ONLY place `useChatsArchive()` is called for the cockpit. Two
// consequences it exists to guarantee:
//
//   * FR-1.1 — no second fetch path. The bucketed fetch, the keyset pagination
//     and the `conversation_changed` live tail all come from the same controller
//     the Chats destination uses. Nothing here talks to the Transport.
//
//   * NFR-1.1 — one subscription per cockpit, not per open. The hook lives on
//     THIS component, and `RunDestination` keeps it mounted once the panel has
//     been opened even while the panel itself is hidden. Calling the hook inside
//     `ThreadSwitcher` would re-subscribe on every toggle.
//
// It is also a genuine layering seam: `shell/ThreadSwitcher` is pure
// presentation over a structural `ThreadListSource`, so `shell/` never imports
// from `destinations/`.

import type { ConversationId } from "@0x-copilot/api-types";
import type { ReactElement } from "react";

import {
  ThreadSwitcher,
  type ThreadListSource,
  type ThreadSwitcherVariant,
} from "../../shell/ThreadSwitcher";
import { useChatsArchive } from "../chats/useChatsArchive";

export interface ThreadSwitcherHostProps {
  /**
   * Whether the panel is currently shown. The host stays MOUNTED when `false`
   * (that is the point — it holds the subscription); only the panel is dropped.
   */
  readonly open: boolean;
  readonly variant: ThreadSwitcherVariant;
  /** Narrow the DOCKED panel so a small window keeps a usable canvas. */
  readonly compact?: boolean;
  readonly activeConversationId: ConversationId | null;
  readonly onOpenConversation: (id: ConversationId) => void;
  readonly onNewRun?: () => void;
  readonly onRequestClose?: () => void;
  readonly identityName?: string | null;
  readonly id?: string;
}

export function ThreadSwitcherHost({
  open,
  variant,
  compact = false,
  activeConversationId,
  onOpenConversation,
  onNewRun,
  onRequestClose,
  identityName = null,
  id,
}: ThreadSwitcherHostProps): ReactElement | null {
  // Mounted for this component's whole life — including while `open` is false.
  const controller = useChatsArchive();

  if (!open) {
    return null;
  }

  return (
    <ThreadSwitcher
      id={id}
      variant={variant}
      compact={compact}
      // `ChatsArchiveController` structurally satisfies `ThreadListSource`.
      controller={controller as ThreadListSource}
      activeConversationId={activeConversationId}
      onOpenConversation={onOpenConversation}
      onNewRun={onNewRun}
      onRequestClose={onRequestClose}
      identityName={identityName}
    />
  );
}
