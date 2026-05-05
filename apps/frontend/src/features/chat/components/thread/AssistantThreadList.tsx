import type { Conversation } from "@enterprise-search/api-types";
import type { ReactElement } from "react";
import { Sidebar } from "../sidebar/Sidebar";

/**
 * Sidebar mount point.
 *
 * The body of this file used to render `ThreadListPrimitive` directly;
 * PR 2.2 replaced it with `<Sidebar />` (search + grouping + UserCard +
 * keymap) but kept the import surface so `ChatScreen.tsx`'s call site
 * stays unchanged. The three new optional props are passed by ChatScreen
 * to wire `⌘N`, `⌘\`, and thread switching from the sidebar's keymap +
 * UI; not passing them silently degrades to read-only behaviour.
 */
export function AssistantThreadList({
  collapsed,
  conversations,
  loading,
  activeRunId,
  activeConversationId,
  onOpenSettings,
  onRefresh,
  onSwitchToThread,
  onStartNewChat,
  onToggleSidebar,
  onSwitchWorkspace,
}: {
  collapsed: boolean;
  conversations: Conversation[];
  loading: boolean;
  activeRunId: string | null;
  /** PR 2.2 — the conversation id whose run is currently streaming.
   * Drives the sidebar's "live pulse" badge. Optional (and falls back
   * to `null`) so older callers compile unchanged. */
  activeConversationId?: string | null;
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
}): ReactElement {
  return (
    <Sidebar
      collapsed={collapsed}
      conversations={conversations}
      loading={loading}
      activeConversationId={activeConversationId ?? null}
      liveConversationId={
        activeRunId !== null ? (activeConversationId ?? null) : null
      }
      switchingDisabled={activeRunId !== null}
      onSwitchToThread={onSwitchToThread}
      onStartNewChat={onStartNewChat}
      onToggleSidebar={onToggleSidebar}
      onOpenSettings={onOpenSettings}
      onRefresh={onRefresh}
      onSwitchWorkspace={onSwitchWorkspace}
    />
  );
}
