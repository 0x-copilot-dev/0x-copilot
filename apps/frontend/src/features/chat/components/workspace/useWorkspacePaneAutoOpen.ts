// PR 1.5 — workspace pane auto-open trigger.
//
// The pane defaults closed, then auto-opens on the first conversation
// switch where any of the four content tabs has non-zero data. Once the
// user manually toggles the pane, their preference wins; this hook only
// computes whether *the initial state* on conversation switch should be
// open. ChatScreen wires it to a one-shot effect keyed by conversationId.

import { useEffect, useRef } from "react";

export type WorkspacePaneTabId =
  | "sources"
  | "agents"
  | "draft"
  | "approvals"
  | "skills";

export function shouldAutoOpenWorkspacePane(opts: {
  subagentCount: number;
  sourceCount: number;
  draftCount?: number;
  pendingApprovalsCount?: number;
}): boolean {
  return (
    opts.subagentCount > 0 ||
    opts.sourceCount > 0 ||
    (opts.draftCount ?? 0) > 0 ||
    (opts.pendingApprovalsCount ?? 0) > 0
  );
}

/**
 * PR 3.1 — companion of `shouldAutoOpenWorkspacePane` that picks the
 * specific tab to open. Priority: agents (running) > sources > draft >
 * approvals. Pure function, table-tested.
 */
export function autoOpenTab(opts: {
  subagentCount: number;
  sourceCount: number;
  draftCount?: number;
  pendingApprovalsCount?: number;
}): WorkspacePaneTabId | null {
  if (opts.subagentCount > 0) return "agents";
  if (opts.sourceCount > 0) return "sources";
  if ((opts.draftCount ?? 0) > 0) return "draft";
  if ((opts.pendingApprovalsCount ?? 0) > 0) return "approvals";
  return null;
}

export interface WorkspacePaneAutoOpenSignalOptions {
  conversationId: string | null;
  subagentCount: number;
  sourceCount: number;
  draftCount?: number;
  pendingApprovalsCount?: number;
  /** True while the user has explicitly closed the pane for this conversation. */
  suppressed?: boolean;
  /** Receiver — typically `paneState.openOn` from PR 3.2's host. */
  onAutoOpen: (tab: WorkspacePaneTabId) => void;
}

/**
 * PR 3.1 — React-side memory layer over `autoOpenTab`. Fires
 * `onAutoOpen` exactly once per conversation visit when the relevant
 * count first becomes non-zero. PR 3.2 mounts the consumer; until then
 * ChatScreen passes a no-op and the hook is inert.
 */
export function useWorkspacePaneAutoOpenSignal({
  conversationId,
  subagentCount,
  sourceCount,
  draftCount,
  pendingApprovalsCount,
  suppressed,
  onAutoOpen,
}: WorkspacePaneAutoOpenSignalOptions): void {
  const firedFor = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (conversationId === null || suppressed) {
      return;
    }
    if (firedFor.current.has(conversationId)) {
      return;
    }
    const tab = autoOpenTab({
      subagentCount,
      sourceCount,
      draftCount,
      pendingApprovalsCount,
    });
    if (tab === null) {
      return;
    }
    firedFor.current.add(conversationId);
    onAutoOpen(tab);
  }, [
    conversationId,
    subagentCount,
    sourceCount,
    draftCount,
    pendingApprovalsCount,
    suppressed,
    onAutoOpen,
  ]);
}
