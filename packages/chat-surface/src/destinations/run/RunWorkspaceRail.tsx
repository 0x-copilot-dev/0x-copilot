// RunWorkspaceRail — the Run cockpit's tabbed right rail (PR-3.6).
//
// Source: docs/plan/desktop-redesign/phase-3/PRD.md
//   §2 layout ("tabbed right rail `[Chat · Sources · Agents · Approvals]`")
//   FR-3.10 (tab order + Chat default + tablist a11y)
//   FR-3.11 (Chat hosts TcChat; Sources/Agents/Approvals REUSE the hoisted
//            WorkspacePane tab bodies; Draft + Skills MUST NOT appear here)
//   FR-3.12 (Agents "N live" / Approvals pending badges; per-tab empty copy)
//   FR-3.13 (Focus mode collapses the rail to Chat-only, tab chrome suppressed)
//
// RECOMPOSITION, NOT A FORK
// =========================
// This rail does not rebuild the Sources/Agents/Approvals surfaces — it mounts
// the already-hoisted `WorkspacePane` tab BODIES (`SourcesTab`, `AgentsTab`,
// `ApprovalsTab`) and its tablist (`WorkspaceTabs`). The Draft and Skills tabs
// that `WorkspacePane` also owns are intentionally omitted (FR-3.11).
//
// CONTROLLED / INJECTED (single projection — FR-3.3)
// ==================================================
// Like `WorkspacePane`, this is a composition shell that owns NO fetches and NO
// event subscription. Everything flows in via props:
//   - `chatSlot`   — the ONE `TcChat` instance, injected by the host so it
//                    mounts once (FR-3.9). The rail never constructs TcChat, so
//                    switching tabs/modes cannot spawn a second chat mount.
//   - sources / subagents / approvalsQueue — host-reducer outputs (the same
//     shapes `WorkspacePane` consumes). The Run shell threads what it has; a
//     second `useEventProjector` / SSE subscription is NEVER opened here.
//   - counts — DERIVED from the injected maps (no redundant count props that
//     could drift from the data).
//
// SINGLE-MOUNT LAYOUT
// ===================
// The Chat panel (hosting `chatSlot`) is ALWAYS in the tree at a stable child
// position; its visibility is a CSS toggle, never a tree swap. The tablist and
// the Sources/Agents/Approvals panels are the only per-state elements. This
// mirrors `ThreadCanvas`'s mount-once discipline: `chatSlot` survives a
// Studio→Focus→Studio switch AND a Chat→Sources→Chat tab switch (no remount).
//
// Boundary: framework-agnostic — no bare window/document/fetch/localStorage;
// design-system tokens only (sky accent, no lime).

import {
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type { SourceEntry, SubagentEntry } from "@0x-copilot/api-types";

import {
  AgentsTab,
  ApprovalsTab,
  SourcesTab,
  WorkspaceTabs,
  type ApprovalsQueueProjection,
  type SourceEntryMap,
  type SourceRowSlot,
  type SubagentActivitiesByTask,
  type SubagentHistoryGroup,
  type SubagentSnapshotMap,
  type WorkspaceTabsItem,
} from "../../workspace";
import { isRunningStatus } from "../../workspace/workspaceHelpers";
import type { RunMode } from "./useRunMode";

/** The four rail tabs, in DESIGN-SPEC §2 order (Chat is default). */
export type RunRailTabId = "chat" | "sources" | "agents" | "approvals";

const EMPTY_SOURCES: SourceEntryMap = new Map();
const EMPTY_SUBAGENTS: SubagentSnapshotMap = new Map();
const EMPTY_APPROVALS: ApprovalsQueueProjection = { pending: [], recent: [] };

export interface RunWorkspaceRailProps {
  /**
   * Layout mode. `"studio"` shows the full tabset; `"focus"` collapses the rail
   * to the Chat surface only and suppresses the Sources/Agents/Approvals tab
   * chrome (FR-3.13). Aliased to `ThreadMode` — one source of truth.
   */
  readonly mode: RunMode;
  /**
   * The single `TcChat` instance, injected by the host (`RunDestination`) so it
   * mounts ONCE (FR-3.9/FR-3.11). The Chat tab renders exactly this node.
   */
  readonly chatSlot: ReactNode;
  /** Initial selected tab. Defaults to `"chat"` (FR-3.10). */
  readonly defaultTab?: RunRailTabId;
  /**
   * PR-3.7 (FR-3.15/3.16): when the cockpit is scrubbed off-now, the Approvals
   * tab is suppressed — you cannot approve a past state. If Approvals was the
   * active tab it falls back to Chat; snap-to-now (`scrubbed=false`) restores
   * the tab. Default `false` (live). Chat/Sources/Agents are unaffected — you
   * can still inspect a past state's sources/agents.
   */
  readonly scrubbed?: boolean;

  // ── Sources tab (WorkspacePane body inputs) ────────────────────────────
  readonly sources?: SourceEntryMap;
  readonly sourcesLoading?: boolean;
  readonly sourcesError?: string | null;
  readonly sourcesSearching?: boolean;
  readonly onSelectSource?: (source: SourceEntry) => void;
  readonly onJumpToChatSource?: (source: SourceEntry) => void;
  /** Row renderer slot — the web host passes its preview-wired wrapper. */
  readonly SourceRowComponent?: SourceRowSlot;

  // ── Agents tab (WorkspacePane body inputs) ─────────────────────────────
  readonly subagents?: SubagentSnapshotMap;
  readonly subagentsLoading?: boolean;
  readonly subagentsError?: string | null;
  readonly onJumpToSubagent?: (subagent: SubagentEntry) => void;
  readonly subagentActivitiesByTask?: SubagentActivitiesByTask;
  readonly subagentHistoryGroups?: readonly SubagentHistoryGroup[];

  // ── Approvals tab (WorkspacePane body inputs) ──────────────────────────
  readonly approvalsQueue?: ApprovalsQueueProjection;
  /** Jump to the inline `ApprovalCard` in the conversation (Atlas rule). */
  readonly onJumpToApproval?: (approvalId: string, messageId: string) => void;

  /**
   * PR-3.10 SEAM (do not build here): inline approve/reject resolution +
   * Focus-mode `.conf-card` confirmation cards. Threaded through so PR-3.10 can
   * wire them without changing this signature; unused in PR-3.6.
   */
  readonly onApprove?: (approvalId: string) => void;
  readonly onReject?: (approvalId: string) => void;
}

export function RunWorkspaceRail(props: RunWorkspaceRailProps): ReactElement {
  const {
    mode,
    chatSlot,
    defaultTab = "chat",
    sources = EMPTY_SOURCES,
    sourcesLoading,
    sourcesError = null,
    sourcesSearching,
    onSelectSource,
    onJumpToChatSource,
    SourceRowComponent,
    subagents = EMPTY_SUBAGENTS,
    subagentsLoading,
    subagentsError = null,
    onJumpToSubagent,
    subagentActivitiesByTask,
    subagentHistoryGroups,
    approvalsQueue = EMPTY_APPROVALS,
    onJumpToApproval,
    scrubbed = false,
  } = props;

  // Internal, survives mode/tab switches (the rail is never remounted across
  // mode changes — the host re-renders it with a new `mode`).
  const [activeTab, setActiveTab] = useState<RunRailTabId>(defaultTab);

  const isStudio = mode === "studio";
  // PR-3.7 (FR-3.15): approvals are hidden while scrubbed off-now. If Approvals
  // was the active tab, fall back to Chat so the panel does not linger without
  // its tab. Focus mode is ALWAYS Chat; Studio honors the selected tab (FR-3.13).
  const effectiveTab: RunRailTabId = isStudio
    ? scrubbed && activeTab === "approvals"
      ? "chat"
      : activeTab
    : "chat";
  const chatVisible = effectiveTab === "chat";

  // Counts (FR-3.12) derived from the injected maps — same semantics as
  // WorkspacePane so the two rails never disagree.
  const runningAgents = countRunning(subagents);
  const agentsCount = subagents.size;
  const pendingApprovals = approvalsQueue.pending.length;

  const tabItems: WorkspaceTabsItem<RunRailTabId>[] = [
    { id: "chat", label: "Chat" },
    { id: "sources", label: "Sources" },
    {
      id: "agents",
      label: "Agents",
      badge: agentsBadge(runningAgents, agentsCount),
    },
    // PR-3.7: the Approvals tab drops out of the tablist while scrubbed.
    ...(scrubbed
      ? []
      : [
          {
            id: "approvals" as const,
            label: "Approvals",
            badge: approvalsBadge(pendingApprovals),
          },
        ]),
  ];

  return (
    <div
      data-testid="run-workspace-rail"
      data-mode={mode}
      data-active-tab={effectiveTab}
      data-approvals-hidden={scrubbed ? "true" : "false"}
      style={railStyle}
    >
      {/* Tab chrome is suppressed in Focus — the rail is Chat-only (FR-3.13). */}
      {isStudio ? (
        <div style={tablistRowStyle}>
          <WorkspaceTabs
            items={tabItems}
            active={activeTab}
            onSelect={(id) => setActiveTab(id)}
            ariaLabel="Run workspace tabs"
          />
        </div>
      ) : null}

      {/* Chat panel — ALWAYS mounted at a stable position so `chatSlot`
          (the single TcChat) survives every tab/mode switch (FR-3.9). Hidden
          via CSS when another Studio tab is active; never unmounted. */}
      <div
        data-testid="run-rail-panel-chat"
        role={isStudio ? "tabpanel" : undefined}
        aria-label="Chat"
        aria-hidden={!chatVisible}
        style={panelStyle(chatVisible)}
      >
        {chatSlot}
      </div>

      {/* Sources / Agents / Approvals — Studio only; conditionally rendered
          (they carry no scroll/composer state worth preserving), each reusing
          the hoisted WorkspacePane body (which owns its own empty copy). */}
      {isStudio && effectiveTab === "sources" ? (
        <div
          data-testid="run-rail-panel-sources"
          role="tabpanel"
          aria-label="Sources"
          style={panelStyle(true)}
        >
          <SourcesTab
            sources={sources}
            loading={sourcesLoading}
            error={sourcesError}
            searching={sourcesSearching}
            onSelect={onSelectSource}
            onJumpToChat={onJumpToChatSource}
            SourceRowComponent={SourceRowComponent}
          />
        </div>
      ) : null}

      {isStudio && effectiveTab === "agents" ? (
        <div
          data-testid="run-rail-panel-agents"
          role="tabpanel"
          aria-label="Agents"
          style={panelStyle(true)}
        >
          <AgentsTab
            subagents={subagents}
            loading={subagentsLoading}
            error={subagentsError}
            onJumpToSubagent={onJumpToSubagent}
            activitiesByTask={subagentActivitiesByTask}
            historyGroups={subagentHistoryGroups}
          />
        </div>
      ) : null}

      {isStudio && effectiveTab === "approvals" ? (
        <div
          data-testid="run-rail-panel-approvals"
          role="tabpanel"
          aria-label="Approvals"
          style={panelStyle(true)}
        >
          <ApprovalsTab
            queue={approvalsQueue}
            onJumpToApproval={onJumpToApproval}
          />
        </div>
      ) : null}
    </div>
  );
}

// ============================================================
// Count badges (FR-3.12)
// ============================================================

/** Count in-flight subagents (queued / running; paused is NOT running). */
function countRunning(subagents: SubagentSnapshotMap): number {
  let running = 0;
  for (const entry of subagents.values()) {
    if (isRunningStatus(entry.status)) {
      running += 1;
    }
  }
  return running;
}

/**
 * Agents badge: `"N live"` while any subagent runs, else the total once
 * subagents exist. Undefined (no badge) when the tab is empty — the empty
 * AgentsTab copy carries the "nothing here yet" story instead (FR-3.12).
 */
function agentsBadge(running: number, total: number): ReactNode {
  if (running > 0) {
    return (
      <span
        data-testid="run-rail-agents-badge"
        aria-label={`${running} running subagents`}
      >
        {running} live
      </span>
    );
  }
  if (total > 0) {
    return <span data-testid="run-rail-agents-badge">{total}</span>;
  }
  return undefined;
}

/** Approvals badge: the pending count, rendered in the accent token. */
function approvalsBadge(pending: number): ReactNode {
  if (pending <= 0) {
    return undefined;
  }
  return (
    <span
      data-testid="run-rail-approvals-badge"
      data-tone="accent"
      aria-label={`${pending} pending approvals`}
      style={approvalsBadgeStyle}
    >
      {pending}
    </span>
  );
}

// ============================================================
// Styles (design-system tokens only — sky accent, no lime)
// ============================================================

const railStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  height: "100%",
  minHeight: 0,
  minWidth: 0,
  overflow: "hidden",
  background: "var(--color-bg-elevated, #16181f)",
  color: "var(--color-text, #f4f5f6)",
  fontFamily: "var(--font-sans)",
};

const tablistRowStyle: CSSProperties = {
  flexShrink: 0,
  borderBottom: "1px solid var(--color-border, #22252e)",
  background: "var(--color-bg-elevated, #16181f)",
};

const panelStyle = (visible: boolean): CSSProperties => ({
  display: visible ? "flex" : "none",
  flexDirection: "column",
  flex: 1,
  minHeight: 0,
  minWidth: 0,
  overflow: "auto",
});

const approvalsBadgeStyle: CSSProperties = {
  color: "var(--color-accent, #5fb2ec)",
  fontWeight: 600,
};
