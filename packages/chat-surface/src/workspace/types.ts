// PR-1.7 — chat-surface-local boundary types for the hoisted workspace pane.
//
// FR-1.27: any prop the pane / tabs take that the host expresses via a
// `chatModel/*` or host-hook type MUST be re-typed here (or against
// `@0x-copilot/api-types`) so chat-surface never imports apps/frontend. The
// host keeps the hooks that PRODUCE these values (`useWorkspacePaneState`,
// `useApprovalsQueue`, `useSubagentActivities`, `useWorkspacePaneAutoOpen`);
// their return types are structurally identical to the copies below, so the
// host's `WorkspacePaneState` / `ApprovalsQueueProjection` / … flow into the
// hoisted pane's props unchanged. Unifying the two homes is deferred (same
// deferral the PRD applies to `depth.ts` / `subagentHelpers.ts`).

import type { SubagentEntry } from "@0x-copilot/api-types";

import type { SubagentActivityRecord } from "../subagents";

// ── from apps/frontend/.../workspace/useWorkspacePaneAutoOpen.ts ──────────

export type WorkspacePaneTabId =
  | "sources"
  | "agents"
  | "draft"
  | "approvals"
  | "skills";

// ── from apps/frontend/.../workspace/useWorkspacePaneState.ts ─────────────

export type WorkspacePaneCloseReason = "manual" | "viewport";

export interface WorkspacePaneOpenOptions {
  /** Citation id to focus once the Sources tab mounts (chip-click). */
  focusCitationId?: string | null;
  /** Subagent task_id to focus once the Agents tab mounts. */
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

// ── from apps/frontend/.../workspace/useApprovalsQueue.ts ─────────────────

export interface ApprovalsQueueItem {
  approvalId: string;
  /** Tool action's "what does this do" copy. */
  title: string;
  /** Optional sub-line; pulled from `args.summary` / `args.message`. */
  summary: string | null;
  approvalKind:
    | "tool_action"
    | "mcp_tool"
    | "mcp_auth"
    | "ask_a_question"
    | "unknown";
  /** Run id the approval belongs to (so click-to-jump can locate the message). */
  runId: string | null;
  /** Message id the approval part lives on. */
  messageId: string;
  /** Resolved? When true the row is in `recent`. */
  resolved: boolean;
  /** ISO timestamp of resolution; null when pending. */
  resolvedAt: string | null;
  /** Connector / target preview ("#launch-aurora", "Notion / Drafts"). */
  target: string | null;
}

export interface ApprovalsQueueProjection {
  pending: readonly ApprovalsQueueItem[];
  recent: readonly ApprovalsQueueItem[];
}

// ── from apps/frontend/.../workspace/useSubagentActivities.ts ─────────────

export type SubagentActivitiesByTask = ReadonlyMap<
  string,
  readonly SubagentActivityRecord[]
>;

export interface SubagentHistoryGroup {
  id: string;
  label: string;
  timestamp: string | null;
  entries: readonly SubagentEntry[];
}
