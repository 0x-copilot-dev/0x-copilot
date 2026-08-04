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
//
// D-1.4 (projects scope) rides the same two properties. The scope reaches the
// CONTROLLER, not the panel: `useChatsArchive` narrows the page-1 fetch, every
// keyset page and the live tail together, whereas a filter applied to the
// rendered list could only drop rows the server had already spent the page
// budget on. And because the hook call did not move, changing scope re-fetches
// but does NOT re-subscribe per open — NFR-1.1 survives the new axis.

import type { ConversationId, ProjectId } from "@0x-copilot/api-types";
import type { ReactElement } from "react";

import {
  ThreadSwitcher,
  type ThreadListSource,
  type ThreadScopeOption,
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
  /**
   * Project the list is narrowed to; `null` (the default) = All threads.
   *
   * Host-owned, like the two below: the cockpit neither fetches the project
   * list nor decides the scope. The same value also qualifies "New run", so the
   * host is the only place that can keep filing and filtering in step.
   */
  readonly scope?: ProjectId | null;
  /** Projects the list can be scoped to. Empty/absent → no scope control. */
  readonly scopeOptions?: ReadonlyArray<ThreadScopeOption>;
  readonly onScopeChange?: (next: ProjectId | null) => void;
  readonly onRequestClose?: () => void;
  readonly id?: string;
}

export function ThreadSwitcherHost({
  open,
  variant,
  compact = false,
  activeConversationId,
  onOpenConversation,
  onNewRun,
  scope = null,
  scopeOptions,
  onScopeChange,
  onRequestClose,
  id,
}: ThreadSwitcherHostProps): ReactElement | null {
  // Mounted for this component's whole life — including while `open` is false.
  //
  // The options literal is deliberately NOT memoised: `useChatsArchive`
  // normalises `projectId` to a primitive before it reaches any dependency
  // array, so what drives a refetch is the scope's VALUE, not this object's
  // identity. A `useMemo` here would suggest the opposite guarantee.
  const controller = useChatsArchive({ projectId: scope });

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
      scope={scope}
      scopeOptions={scopeOptions}
      onScopeChange={onScopeChange}
      onRequestClose={onRequestClose}
    />
  );
}
