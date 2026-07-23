import type { Conversation } from "@0x-copilot/api-types";
import { IconButton } from "@0x-copilot/design-system";
import { useId, useMemo, useRef, useState, type ReactElement } from "react";
import { useKeymap } from "../../../../app/keymap";
import { useApprovalFocus } from "../../approval/ApprovalFocusContext";
import { filterConversations } from "../../utils/filterConversations";
import { LogoMark } from "../thread/LogoMark";
import { ConversationListGroups } from "./ConversationListGroups";
import { SidebarSearch } from "./SidebarSearch";
import { UserCard } from "./UserCard";

/**
 * Sidebar surface (PR 2.2 + PR 2.2.1) — replaces the body of
 * `AssistantThreadList`.
 *
 * Owns:
 *   - search query state (ephemeral; not persisted across reloads),
 *   - the four global keymap bindings (`$mod+N` / `$mod+K` / `$mod+\\`
 *     / `$mod+Enter`), wired through optional callbacks so non-chat
 *     surfaces don't need to feed handlers,
 *   - composition of LogoMark / SidebarSearch / list groups / UserCard.
 *
 * Reads (no fetches in this layer):
 *   - `conversations` is the same `Conversation[]` already in
 *     `ChatScreen.tsx` state,
 *   - `activeConversationId` is the visible conversation,
 *   - `liveConversationIds` is the set of conversations currently
 *     running a backgrounded SSE stream (PR 2.2.1). Replaces the prior
 *     singleton `liveConversationId` and the `switchingDisabled` gate —
 *     rows are always clickable now.
 *
 * Switching conversations and starting a new one are handed back to the
 * parent (the runtime owns the actual switch). When the consumer doesn't
 * pass a handler the corresponding chord is silently a no-op — keeps
 * the component testable in isolation.
 */
export function Sidebar({
  collapsed,
  conversations,
  loading,
  activeConversationId,
  liveConversationIds,
  onSwitchToThread,
  onStartNewChat,
  onToggleSidebar,
  onOpenSettings,
  onRefresh,
  onSwitchWorkspace,
  onTogglePin,
  onArchive,
  now,
}: SidebarProps): ReactElement {
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const listId = useId();
  const approvalFocus = useApprovalFocus();

  const filtered = useMemo(
    () => filterConversations(conversations, query),
    [conversations, query],
  );

  const bindings = useMemo(
    () => ({
      "$mod+N": () => {
        onStartNewChat?.();
      },
      "$mod+K": {
        bypassInputFocus: true,
        handler: () => {
          // Auto-uncollapse + focus search input so a single keystroke
          // works no matter the sidebar state.
          if (collapsed) {
            onToggleSidebar?.();
          }
          searchRef.current?.focus();
        },
      },
      "$mod+\\": () => {
        onToggleSidebar?.();
      },
      "$mod+Enter": () => {
        approveTopmostApproval(approvalFocus);
      },
    }),
    [approvalFocus, collapsed, onStartNewChat, onToggleSidebar],
  );
  useKeymap(bindings);

  return (
    <aside
      className="aui-sidebar"
      data-collapsed={collapsed ? "true" : undefined}
      aria-label="Conversation history"
      aria-hidden={collapsed}
    >
      <div className="aui-sidebar__header">
        <LogoMark />
        {onToggleSidebar ? (
          <IconButton
            type="button"
            aria-label="Hide sidebar"
            data-tooltip="Hide sidebar (⌘\\)"
            onClick={onToggleSidebar}
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <rect x="3" y="3" width="18" height="18" rx="2" />
              <path d="M9 3v18" />
            </svg>
          </IconButton>
        ) : null}
      </div>
      <div className="aui-sidebar__controls">
        <button
          type="button"
          className="aui-new-thread"
          disabled={!onStartNewChat}
          title="Start a new thread (⌘N)"
          onClick={() => onStartNewChat?.()}
        >
          + New chat
        </button>
        <SidebarSearch
          ref={searchRef}
          value={query}
          onChange={setQuery}
          listId={listId}
        />
      </div>
      <div className="aui-sidebar__list" id={listId}>
        {loading ? (
          <p className="aui-sidebar__note">Loading history…</p>
        ) : conversations.length === 0 ? (
          <p className="aui-sidebar__note">No threads yet.</p>
        ) : (
          <ConversationListGroups
            conversations={filtered}
            now={now ?? new Date()}
            activeConversationId={activeConversationId}
            liveConversationIds={liveConversationIds}
            onSelect={(id) => onSwitchToThread?.(id)}
            onTogglePin={onTogglePin}
            onArchive={onArchive}
          />
        )}
      </div>
      <div className="aui-sidebar__footer">
        <UserCard
          onOpenSettings={onOpenSettings}
          onSwitchWorkspace={onSwitchWorkspace}
        />
      </div>
    </aside>
  );
}

export interface SidebarProps {
  collapsed: boolean;
  conversations: readonly Conversation[];
  loading: boolean;
  activeConversationId: string | null;
  /**
   * PR 2.2.1 — set of conversation_ids whose runtime slot has a live
   * (non-terminal) run. Replaces the singleton `liveConversationId` so
   * any number of chats can pulse simultaneously.
   */
  liveConversationIds: ReadonlySet<string>;
  onSwitchToThread?: (conversationId: string) => void;
  onStartNewChat?: () => void;
  onToggleSidebar?: () => void;
  onOpenSettings: () => void;
  onRefresh: () => void;
  onSwitchWorkspace?: (orgId: string) => void;
  /**
   * PRD-09 D2 — pin / unpin a conversation, persisted on the first-class
   * `pinned` column via `POST /v1/agent/conversations/{id}/pin`. Optional so
   * non-chat surfaces (storybook) skip the affordance. The grouping reads
   * `conversation.pinned` directly — no client-side pinned-id set.
   */
  onTogglePin?: (conversationId: string, nextPinned: boolean) => void;
  /** PR F3 — archive a conversation. Server-side flips `archived_at`. */
  onArchive?: (conversationId: string) => void;
  /** Injected for tests; ChatScreen passes `new Date()`. */
  now?: Date;
}

function approveTopmostApproval(api: { approveTopmost: () => boolean }): void {
  const handled = api.approveTopmost();
  if (!handled) {
    // Polite hint via the document `<body>` aria-live region. No toast
    // dependency; we keep this reach-around tiny.
    if (typeof document === "undefined") {
      return;
    }
    const region = ensurePoliteRegion();
    region.textContent = "No approval to confirm.";
    window.setTimeout(() => {
      if (region.textContent === "No approval to confirm.") {
        region.textContent = "";
      }
    }, 2000);
  }
}

function ensurePoliteRegion(): HTMLElement {
  const existing = document.getElementById("aui-keymap-live");
  if (existing) {
    return existing;
  }
  const region = document.createElement("div");
  region.id = "aui-keymap-live";
  region.setAttribute("role", "status");
  region.setAttribute("aria-live", "polite");
  region.style.position = "absolute";
  region.style.width = "1px";
  region.style.height = "1px";
  region.style.overflow = "hidden";
  region.style.clip = "rect(0 0 0 0)";
  region.style.clipPath = "inset(50%)";
  document.body.appendChild(region);
  return region;
}
