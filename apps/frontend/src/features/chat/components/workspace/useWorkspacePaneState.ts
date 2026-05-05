// PR 3.2 — single source of truth for workspace-pane open/closed state.
//
// `open`, `activeTab`, and per-conversation "user manually closed me"
// memory live here. `openOn(tab, opts)` is the same hook the auto-open
// signal (PR 1.5/3.1 `useWorkspacePaneAutoOpenSignal`) calls and the one
// the topbar panel toggle / pane close button calls. Behaviour:
//
//   - `open` is global (one boolean for the whole ChatScreen). When the
//     pane is open it shows whichever conversation the user is on.
//   - `activeTab` is global too; it's a *current view selection*, not a
//     per-conversation property. Switching conversations doesn't reset
//     it (the tab the user last selected remains the right default).
//   - A user-driven `close("manual")` poisons the per-conversation
//     auto-open memory: the auto-open signal will not re-open the pane
//     for that conversation in this session. A `close("viewport")` (the
//     responsive-overlay close path) does NOT poison.
//   - Switching conversations clears nothing — each conversation's
//     manual-close memory persists per session, and the auto-open hook
//     evaluates the new conversation against the existing memory set.
//
// The reducer is intentionally tiny; lifting it into a hook keeps
// ChatScreen's prop drilling honest (Topbar.panelOpen / pane-close /
// auto-open signal all read and write the same place).

import { useCallback, useMemo, useRef, useState } from "react";

import type { WorkspacePaneTabId } from "./useWorkspacePaneAutoOpen";

export type WorkspacePaneCloseReason = "manual" | "viewport";

export interface WorkspacePaneOpenOptions {
  /** Citation id to focus once Sources tab mounts (PR 3.1 chip-click). */
  focusCitationId?: string | null;
  /** Subagent task_id to focus once Agents tab mounts. */
  focusSubagentTaskId?: string | null;
}

export interface WorkspacePaneFocus {
  citationId?: string | null;
  subagentTaskId?: string | null;
}

export interface WorkspacePaneState {
  open: boolean;
  activeTab: WorkspacePaneTabId;
  focus: WorkspacePaneFocus;
  /** Open the pane on a specific tab (idempotent if already open + on tab). */
  openOn: (tab: WorkspacePaneTabId, opts?: WorkspacePaneOpenOptions) => void;
  /** Close the pane. `manual` poisons auto-open memory for the conversation. */
  close: (reason: WorkspacePaneCloseReason) => void;
  /** Toggle helper. Manual when closing. */
  toggle: () => void;
  /** Switch the active tab without changing open/closed. */
  setActiveTab: (tab: WorkspacePaneTabId) => void;
  /** True iff the user has manually closed the pane on this conversation
   *  during this session. Read by the auto-open signal. */
  isAutoOpenSuppressed: (conversationId: string | null) => boolean;
}

export interface UseWorkspacePaneStateOptions {
  /** Active conversation id; manual-close is keyed by this. */
  conversationId: string | null;
  /** Initial open state. Default: false. */
  initialOpen?: boolean;
  /** Initial active tab. Default: "sources". */
  initialTab?: WorkspacePaneTabId;
}

export function useWorkspacePaneState({
  conversationId,
  initialOpen = false,
  initialTab = "sources",
}: UseWorkspacePaneStateOptions): WorkspacePaneState {
  const [open, setOpen] = useState(initialOpen);
  const [activeTab, setActiveTabState] =
    useState<WorkspacePaneTabId>(initialTab);
  const [focus, setFocus] = useState<WorkspacePaneFocus>({});
  const manuallyClosedRef = useRef<Set<string>>(new Set());

  const openOn = useCallback<WorkspacePaneState["openOn"]>(
    (tab, opts) => {
      // Re-opening clears the manual-close memory for the conversation
      // — the user's intent superseded their earlier close.
      if (conversationId !== null) {
        manuallyClosedRef.current.delete(conversationId);
      }
      setOpen(true);
      setActiveTabState(tab);
      setFocus({
        citationId: opts?.focusCitationId ?? null,
        subagentTaskId: opts?.focusSubagentTaskId ?? null,
      });
    },
    [conversationId],
  );

  const close = useCallback<WorkspacePaneState["close"]>(
    (reason) => {
      if (reason === "manual" && conversationId !== null) {
        manuallyClosedRef.current.add(conversationId);
      }
      setOpen(false);
      setFocus({});
    },
    [conversationId],
  );

  const toggle = useCallback<WorkspacePaneState["toggle"]>(() => {
    setOpen((current) => {
      if (current) {
        // Closing via toggle is a manual close.
        if (conversationId !== null) {
          manuallyClosedRef.current.add(conversationId);
        }
        setFocus({});
        return false;
      }
      // Opening via toggle clears the suppression so the auto-open
      // signal can fire again in this conversation if it has data.
      if (conversationId !== null) {
        manuallyClosedRef.current.delete(conversationId);
      }
      return true;
    });
  }, [conversationId]);

  const setActiveTab = useCallback<WorkspacePaneState["setActiveTab"]>(
    (tab) => {
      setActiveTabState(tab);
      setFocus({});
    },
    [],
  );

  const isAutoOpenSuppressed = useCallback((cid: string | null): boolean => {
    if (cid === null) {
      return false;
    }
    return manuallyClosedRef.current.has(cid);
  }, []);

  return useMemo<WorkspacePaneState>(
    () => ({
      open,
      activeTab,
      focus,
      openOn,
      close,
      toggle,
      setActiveTab,
      isAutoOpenSuppressed,
    }),
    [
      open,
      activeTab,
      focus,
      openOn,
      close,
      toggle,
      setActiveTab,
      isAutoOpenSuppressed,
    ],
  );
}
