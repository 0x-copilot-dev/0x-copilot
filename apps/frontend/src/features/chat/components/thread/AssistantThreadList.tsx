import type { Conversation } from "@0x-copilot/api-types";
import type { ReactElement } from "react";
import { Sidebar } from "../sidebar/Sidebar";

/**
 * Sidebar mount point.
 *
 * The body of this file used to render `ThreadListPrimitive` directly;
 * PR 2.2 replaced it with `<Sidebar />`. PR 2.2.1 widened the live
 * signal from a single conversation id (driven by the visible
 * `activeRunId`) into a set, so background runs across non-visible
 * chats can surface in the sidebar simultaneously, and dropped the
 * `switchingDisabled` gate now that switching mid-run is a supported
 * lifecycle.
 */
export function AssistantThreadList({
  collapsed,
  conversations,
  loading,
  activeConversationId,
  liveConversationIds,
  onOpenSettings,
  onRefresh,
  onSwitchToThread,
  onStartNewChat,
  onToggleSidebar,
  onSwitchWorkspace,
  onTogglePin,
  onArchive,
  pinnedIds,
}: {
  collapsed: boolean;
  conversations: Conversation[];
  loading: boolean;
  /** The visible conversation. Drives the active row highlight. */
  activeConversationId?: string | null;
  /** PR 2.2.1 — every conversation with a live (non-terminal) run.
   * Drives the sidebar's "live pulse" badge for both the visible chat
   * and any backgrounded ones. */
  liveConversationIds: ReadonlySet<string>;
  onOpenSettings: () => void;
  onRefresh: () => void;
  /** PR 2.2 — switch threads programmatically (sidebar row click + ⌘N). */
  onSwitchToThread?: (conversationId: string) => void;
  /** PR 2.2 — start a new chat from `+ New chat` and from `⌘N`. */
  onStartNewChat?: () => void;
  /** PR 2.2 — toggle the sidebar collapsed state from `⌘\\`. */
  onToggleSidebar?: () => void;
  /** PR 2.2 — switch workspace from the UserCard popover. */
  onSwitchWorkspace?: (orgId: string) => void;
  /** PR F3 — pin / unpin from row overflow menu. */
  onTogglePin?: (conversationId: string, nextPinned: boolean) => void;
  /** PR F3 — archive from row overflow menu. */
  onArchive?: (conversationId: string) => void;
  /** PR F3 — pinned conversation_id set (localStorage source of truth). */
  pinnedIds?: ReadonlySet<string>;
}): ReactElement {
  return (
    <Sidebar
      collapsed={collapsed}
      conversations={conversations}
      loading={loading}
      activeConversationId={activeConversationId ?? null}
      liveConversationIds={liveConversationIds}
      onSwitchToThread={onSwitchToThread}
      onStartNewChat={onStartNewChat}
      onToggleSidebar={onToggleSidebar}
      onOpenSettings={onOpenSettings}
      onRefresh={onRefresh}
      onSwitchWorkspace={onSwitchWorkspace}
      onTogglePin={onTogglePin}
      onArchive={onArchive}
      pinnedIds={pinnedIds}
    />
  );
}
