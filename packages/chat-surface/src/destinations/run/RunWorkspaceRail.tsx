// RunWorkspaceRail — the Run cockpit's tabbed right rail (PR-3.6).
//
// Source: docs/plan/desktop-redesign/phase-3/PRD.md
//   §2 layout ("tabbed right rail"); tab order follows the v3 mockup
//     (copilot-v3.css): `[Chat · Agents · Approvals · Sources]`
//   FR-3.10 (Chat default + tablist a11y)
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
  useEffect,
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type {
  PendingAgentRow,
  SourceEntry,
  SubagentEntry,
} from "@0x-copilot/api-types";

import {
  AgentFleetList,
  AgentsTab,
  ApprovalsTab,
  LedgerSourcesTab,
  PendingCardList,
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
import type { PendingCard } from "./pendingCardsProjection";
import type { LedgerSourcesProjection } from "./projectLedgerSources";
import type { RunMode } from "./useRunMode";

/** The four rail tabs, in v3 order — Chat · Agents · Approvals · Sources
 *  (copilot-v3.css). Chat is the default. */
export type RunRailTabId = "chat" | "agents" | "approvals" | "sources";

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
  /**
   * Generative Surfaces v2 (PRD-E1 / FR-E3). When non-null, the Sources panel
   * mounts the ledger-fold `LedgerSourcesTab` (everything read this run, grouped
   * by connector) INSTEAD of the legacy citation `SourcesTab`. Absent / null ⇒
   * existing behavior is byte-identical (all pre-existing assertions untouched).
   */
  readonly ledgerSources?: LedgerSourcesProjection | null;
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
   * Generative Surfaces v2 (PRD-E2). When present, the Approvals panel renders
   * the cross-run `<PendingCardList>` ABOVE the existing v1 `ApprovalsTab`, the
   * approvals badge count ADDS `cards.length`, and the Agents panel renders the
   * `<AgentFleetList>` ABOVE the existing subagent `AgentsTab` (subagents stay —
   * they are this run's fleet detail). Absent ⇒ the rail is byte-identical to
   * today (flag off ⇒ this prop is never constructed by the host).
   */
  readonly pendingV2?: {
    readonly cards: readonly PendingCard[];
    readonly agents: readonly PendingAgentRow[];
    readonly onReview: (card: PendingCard) => void;
    readonly onOpenRun: (agent: PendingAgentRow) => void;
    /** The run currently open in the cockpit — marked "This run" in the fleet. */
    readonly currentRunId: string | null;
  };

  /**
   * Generative Surfaces v2 (PRD-E2 / FR-F3): a monotonically-increasing nonce the
   * host bumps to command the rail onto the Approvals tab (the `PendingCounterChip`
   * "N waiting" chip lives in the cockpit header, outside this rail, so it drives
   * the tab through this one-directional signal). Every increase switches the
   * active tab to Approvals; the initial value is ignored so a first render never
   * force-selects it. Absent ⇒ unchanged (byte-identical when the flag is off).
   */
  readonly focusApprovalsSignal?: number;

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
    ledgerSources = null,
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
    pendingV2,
    focusApprovalsSignal,
  } = props;

  // Internal, survives mode/tab switches (the rail is never remounted across
  // mode changes — the host re-renders it with a new `mode`).
  const [activeTab, setActiveTab] = useState<RunRailTabId>(defaultTab);

  // PRD-E2: the header "N waiting" chip commands the Approvals tab through a
  // one-directional nonce. The initial value never force-selects (only an
  // increase does), so mounting with the signal set is inert until it bumps.
  useEffect(() => {
    if (focusApprovalsSignal === undefined || focusApprovalsSignal <= 0) {
      return;
    }
    setActiveTab("approvals");
  }, [focusApprovalsSignal]);

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
  // PRD-E2: the cross-run pending cards ADD to the v1 approvals count so the
  // badge reflects everything in the one queue (absent ⇒ +0, byte-identical).
  const pendingV2Count = pendingV2?.cards.length ?? 0;
  const pendingApprovals = approvalsQueue.pending.length + pendingV2Count;

  // v3 order (copilot-v3.css): Chat · Agents · Approvals · Sources.
  const tabItems: WorkspaceTabsItem<RunRailTabId>[] = [
    { id: "chat", label: "Chat" },
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
    { id: "sources", label: "Sources" },
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
          {ledgerSources !== null ? (
            <LedgerSourcesTab ledgerSources={ledgerSources} />
          ) : (
            <SourcesTab
              sources={sources}
              loading={sourcesLoading}
              error={sourcesError}
              searching={sourcesSearching}
              onSelect={onSelectSource}
              onJumpToChat={onJumpToChatSource}
              SourceRowComponent={SourceRowComponent}
            />
          )}
        </div>
      ) : null}

      {isStudio && effectiveTab === "agents" ? (
        <div
          data-testid="run-rail-panel-agents"
          role="tabpanel"
          aria-label="Agents"
          style={panelStyle(true)}
        >
          {/* PRD-E2 — the fleet view leads; the subagent detail stays below. */}
          {pendingV2 !== undefined ? (
            <AgentFleetList
              agents={pendingV2.agents}
              currentRunId={pendingV2.currentRunId}
              onOpenRun={pendingV2.onOpenRun}
            />
          ) : null}
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
          {/* PRD-E2 — the cross-run queue leads; the v1 in-chat approvals
              (this conversation's) stay below. */}
          {pendingV2 !== undefined ? (
            <PendingCardList
              cards={pendingV2.cards}
              onReview={pendingV2.onReview}
            />
          ) : null}
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
