import type { Conversation } from "@enterprise-search/api-types";
import { IconButton } from "@enterprise-search/design-system";
import { useId, useMemo, useRef, useState, type ReactElement } from "react";
import { useKeymap } from "../../../../app/keymap";
import { useApprovalFocus } from "../../approval/ApprovalFocusContext";
import { filterConversations } from "../../utils/filterConversations";
import { LogoMark } from "../thread/LogoMark";
import { ConversationListGroups } from "./ConversationListGroups";
import { SidebarSearch } from "./SidebarSearch";
import { UserCard } from "./UserCard";

/**
 * Sidebar surface (PR 2.2) — replaces the body of `AssistantThreadList`.
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
 *   - `activeConversationId` is the conversation that owns the active
 *     run, used to render the "live pulse" badge.
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
  liveConversationId,
  switchingDisabled,
  onSwitchToThread,
  onStartNewChat,
  onToggleSidebar,
  onOpenSettings,
  onRefresh,
  onSwitchWorkspace,
  onTogglePin,
  onArchive,
  pinnedIds,
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
            ⤺
          </IconButton>
        ) : null}
      </div>
      <div className="aui-sidebar__controls">
        <button
          type="button"
          className="aui-new-thread"
          disabled={!onStartNewChat || switchingDisabled}
          title={
            switchingDisabled
              ? "Stop the current response before starting a new chat"
              : "Start a new thread (⌘N)"
          }
          onClick={() => onStartNewChat?.()}
        >
          + New chat
          <span className="aui-new-thread__chord" aria-hidden="true">
            ⌘N
          </span>
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
            liveConversationId={liveConversationId}
            switchingDisabled={switchingDisabled}
            onSelect={(id) => onSwitchToThread?.(id)}
            onTogglePin={onTogglePin}
            onArchive={onArchive}
            pinnedIds={pinnedIds}
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
  liveConversationId: string | null;
  /** True while a run is streaming — switching threads is suppressed. */
  switchingDisabled: boolean;
  onSwitchToThread?: (conversationId: string) => void;
  onStartNewChat?: () => void;
  onToggleSidebar?: () => void;
  onOpenSettings: () => void;
  onRefresh: () => void;
  onSwitchWorkspace?: (orgId: string) => void;
  /**
   * PR F3 — pin / unpin a conversation. Persisted on
   * `metadata.pinned`. Optional so non-chat surfaces (storybook) skip
   * the affordance.
   */
  onTogglePin?: (conversationId: string, nextPinned: boolean) => void;
  /** PR F3 — archive a conversation. Server-side flips `archived_at`. */
  onArchive?: (conversationId: string) => void;
  /**
   * PR F3 — set of conversation_ids the local user has pinned. Threads
   * in this set collapse into a `Pinned` group at the top of the
   * sidebar. Source of truth is `usePinnedConversations` (localStorage)
   * until the backend gains a typed `metadata.pinned` column.
   */
  pinnedIds?: ReadonlySet<string>;
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
