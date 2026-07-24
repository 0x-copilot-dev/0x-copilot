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

  /**
   * WS-F (Focus Run-details panel): collapsed state of the Focus-mode
   * Run-details panel. When `true` the panel shrinks to the 46px icon rail
   * (`.sd-strip`); when `false` it shows the full 324px panel (`.sd`) with the
   * Agents/Approvals/Sources SideTabs. Controlled by the host
   * (`RunDestination` via `useRunPanelCollapsed`, KeyValueStore-backed) so it
   * persists per conversation. Omitted → the rail owns the state internally
   * (session-only), so standalone callers still get a working toggle. Ignored
   * in Studio mode (the full tabset is unaffected).
   */
  readonly panelCollapsed?: boolean;
  /**
   * Fired with the next collapsed state when the user toggles the Focus
   * Run-details panel. Omit for a non-persistent, session-only collapse.
   */
  readonly onPanelCollapsedChange?: (collapsed: boolean) => void;
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
    panelCollapsed,
    onPanelCollapsedChange,
  } = props;

  // Internal, survives mode/tab switches (the rail is never remounted across
  // mode changes — the host re-renders it with a new `mode`).
  const [activeTab, setActiveTab] = useState<RunRailTabId>(defaultTab);

  // WS-F: Focus Run-details collapse. Controlled by the host when
  // `panelCollapsed` is supplied (KeyValueStore-persisted per conversation);
  // otherwise a session-only internal fallback so standalone callers still
  // get a working toggle.
  const [collapsedInternal, setCollapsedInternal] = useState(false);
  const collapsed = panelCollapsed ?? collapsedInternal;
  const setCollapsed = (next: boolean): void => {
    if (onPanelCollapsedChange !== undefined) {
      onPanelCollapsedChange(next);
    } else {
      setCollapsedInternal(next);
    }
  };

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
  const isFocus = !isStudio;
  // PR-3.7 (FR-3.15): approvals are hidden while scrubbed off-now. If Approvals
  // was the active tab, fall back to Chat so the panel does not linger without
  // its tab. In Focus the Chat is the LEFT column (always visible) and the
  // Agents/Approvals/Sources SideTabs drive the right Run-details panel — the
  // tab is never forced to Chat anymore (WS-F).
  const effectiveTab: RunRailTabId = isStudio
    ? scrubbed && activeTab === "approvals"
      ? "chat"
      : activeTab
    : "chat";
  const chatVisible = isFocus || effectiveTab === "chat";

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

  // WS-F: the Focus Run-details panel shows one of Agents/Approvals/Sources
  // (never Chat — that is the left column). Derive it from the shared
  // `activeTab` so the header "N waiting" chip's `focusApprovalsSignal`
  // (which sets `activeTab="approvals"`) drives this panel too; fall to Agents
  // for the Chat default and while Approvals is scrubbed away.
  let focusPanelTab: FocusPanelTab =
    activeTab === "chat" || activeTab === "sources"
      ? activeTab === "sources"
        ? "sources"
        : "agents"
      : activeTab;
  if (scrubbed && focusPanelTab === "approvals") {
    focusPanelTab = "agents";
  }
  const focusTabItems: WorkspaceTabsItem<FocusPanelTab>[] = [
    {
      id: "agents",
      label: "Agents",
      badge: agentsBadge(runningAgents, agentsCount),
    },
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

  // Panel bodies — the hoisted WorkspacePane bodies, computed once and reused
  // by BOTH the Studio tabset and the Focus Run-details panel (recomposition,
  // not a fork — FR-3.11). Each owns its own empty copy.
  const sourcesBody: ReactNode =
    ledgerSources !== null ? (
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
    );
  const agentsBody: ReactNode = (
    <>
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
    </>
  );
  const approvalsBody: ReactNode = (
    <>
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
    </>
  );
  const focusPanelBody: ReactNode =
    focusPanelTab === "agents"
      ? agentsBody
      : focusPanelTab === "approvals"
        ? approvalsBody
        : sourcesBody;

  return (
    <div
      data-testid="run-workspace-rail"
      data-mode={mode}
      data-active-tab={effectiveTab}
      data-approvals-hidden={scrubbed ? "true" : "false"}
      data-focus-panel-tab={isFocus ? focusPanelTab : undefined}
      data-focus-panel-collapsed={isFocus && collapsed ? "true" : "false"}
      style={railStyle(mode)}
    >
      {/* Tab chrome is suppressed in Focus — the Studio tabset gives way to the
          Chat | Run-details two-column layout below (WS-F / FR-3.13). */}
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
          (the single TcChat) survives every tab/mode switch AND the
          Studio↔Focus switch (FR-3.9). In Studio it is one of the stacked
          tab panels (hidden via CSS when another tab is active); in Focus it
          is the LEFT column with its content constrained to the 730px centered
          column (`.fx-col`). The inner wrapper is present in BOTH modes so the
          `chatSlot` node keeps its parent chain across the switch (no remount);
          only its style changes. */}
      <div
        data-testid="run-rail-panel-chat"
        role={isStudio ? "tabpanel" : undefined}
        aria-label="Chat"
        aria-hidden={isStudio ? !chatVisible : false}
        style={chatPanelStyle(mode, chatVisible)}
      >
        <div style={chatInnerStyle(mode)}>{chatSlot}</div>
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
          {sourcesBody}
        </div>
      ) : null}

      {isStudio && effectiveTab === "agents" ? (
        <div
          data-testid="run-rail-panel-agents"
          role="tabpanel"
          aria-label="Agents"
          style={panelStyle(true)}
        >
          {agentsBody}
        </div>
      ) : null}

      {isStudio && effectiveTab === "approvals" ? (
        <div
          data-testid="run-rail-panel-approvals"
          role="tabpanel"
          aria-label="Approvals"
          style={panelStyle(true)}
        >
          {approvalsBody}
        </div>
      ) : null}

      {/* WS-F: Focus Run-details panel — the RIGHT column. Expanded (324px):
          a "Run details" header + the Agents/Approvals/Sources SideTabs +
          the selected body (reused above). Collapsed (46px): the icon rail.
          Rendered as a trailing sibling so the Chat panel keeps its stable
          position (Studio → Focus never remounts `chatSlot`). */}
      {isFocus
        ? collapsed
          ? renderFocusStrip({
              pendingApprovals,
              scrubbed,
              onExpand: () => setCollapsed(false),
              onPick: (tab) => {
                setActiveTab(tab);
                setCollapsed(false);
              },
            })
          : renderFocusPanel({
              tabItems: focusTabItems,
              activeTab: focusPanelTab,
              onSelect: (id) => setActiveTab(id),
              onCollapse: () => setCollapsed(true),
              body: focusPanelBody,
            })
        : null}
    </div>
  );
}

// ============================================================
// WS-F — Focus Run-details panel + collapsed icon rail
// ============================================================

/** The Focus Run-details tabs — Chat is the left column, never a tab here. */
type FocusPanelTab = "agents" | "approvals" | "sources";

interface FocusPanelArgs {
  readonly tabItems: readonly WorkspaceTabsItem<FocusPanelTab>[];
  readonly activeTab: FocusPanelTab;
  readonly onSelect: (id: FocusPanelTab) => void;
  readonly onCollapse: () => void;
  readonly body: ReactNode;
}

/** Expanded (324px) Run-details panel (`.sd` in copilot-v3.css). */
function renderFocusPanel(args: FocusPanelArgs): ReactElement {
  const { tabItems, activeTab, onSelect, onCollapse, body } = args;
  return (
    <aside
      data-testid="tc-focus-panel"
      aria-label="Run details"
      style={focusPanelStyle}
    >
      <div style={focusPanelHeaderStyle}>
        <span style={focusPanelTitleStyle}>Run details</span>
        <button
          type="button"
          data-testid="tc-focus-panel-collapse"
          aria-label="Collapse run details"
          aria-expanded={true}
          onClick={onCollapse}
          style={focusIconButtonStyle}
        >
          <ChevronRightIcon />
        </button>
      </div>
      <div style={focusTabsRowStyle}>
        <WorkspaceTabs
          items={tabItems}
          active={activeTab}
          onSelect={onSelect}
          ariaLabel="Run details tabs"
        />
      </div>
      <div
        data-testid={`tc-focus-panel-${activeTab}`}
        role="tabpanel"
        aria-label={FOCUS_TAB_LABELS[activeTab]}
        style={focusPanelBodyStyle}
      >
        {body}
      </div>
    </aside>
  );
}

interface FocusStripArgs {
  readonly pendingApprovals: number;
  readonly scrubbed: boolean;
  readonly onExpand: () => void;
  readonly onPick: (tab: FocusPanelTab) => void;
}

/** Collapsed (46px) Run-details icon rail (`.sd-strip` in copilot-v3.css). */
function renderFocusStrip(args: FocusStripArgs): ReactElement {
  const { pendingApprovals, scrubbed, onExpand, onPick } = args;
  return (
    <aside
      data-testid="tc-focus-strip"
      aria-label="Run details"
      style={focusStripStyle}
    >
      <button
        type="button"
        data-testid="tc-focus-strip-expand"
        aria-label="Expand run details"
        aria-expanded={false}
        onClick={onExpand}
        style={focusStripButtonStyle}
      >
        <ChevronLeftIcon />
      </button>
      <button
        type="button"
        data-testid="tc-focus-strip-agents"
        aria-label="Agents"
        onClick={() => onPick("agents")}
        style={focusStripButtonStyle}
      >
        <AgentsIcon />
      </button>
      {scrubbed ? null : (
        <button
          type="button"
          data-testid="tc-focus-strip-approvals"
          aria-label="Approvals"
          onClick={() => onPick("approvals")}
          style={focusStripButtonStyle}
        >
          <ApprovalsIcon />
          {pendingApprovals > 0 ? (
            <span
              data-testid="tc-focus-strip-approvals-badge"
              data-tone="accent"
              aria-label={`${pendingApprovals} pending approvals`}
              style={focusStripBadgeStyle}
            >
              {pendingApprovals}
            </span>
          ) : null}
        </button>
      )}
      <button
        type="button"
        data-testid="tc-focus-strip-sources"
        aria-label="Sources"
        onClick={() => onPick("sources")}
        style={focusStripButtonStyle}
      >
        <SourcesIcon />
      </button>
    </aside>
  );
}

const FOCUS_TAB_LABELS: Record<FocusPanelTab, string> = {
  agents: "Agents",
  approvals: "Approvals",
  sources: "Sources",
};

// Tiny inline icons (15px, currentColor) — the design-system ships no icon set,
// so the rail owns these locally (same pattern as other chat-surface glyphs).
function iconProps(): {
  readonly width: number;
  readonly height: number;
  readonly viewBox: string;
  readonly fill: "none";
  readonly stroke: "currentColor";
  readonly strokeWidth: number;
  readonly strokeLinecap: "round";
  readonly strokeLinejoin: "round";
  readonly "aria-hidden": true;
} {
  return {
    width: 15,
    height: 15,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": true,
  };
}

function ChevronRightIcon(): ReactElement {
  return (
    <svg {...iconProps()}>
      <polyline points="9 18 15 12 9 6" />
    </svg>
  );
}

function ChevronLeftIcon(): ReactElement {
  return (
    <svg {...iconProps()}>
      <polyline points="15 18 9 12 15 6" />
    </svg>
  );
}

function AgentsIcon(): ReactElement {
  return (
    <svg {...iconProps()}>
      <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
      <path d="M16 3.13a4 4 0 0 1 0 7.75" />
    </svg>
  );
}

function ApprovalsIcon(): ReactElement {
  return (
    <svg {...iconProps()}>
      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
      <polyline points="22 4 12 14.01 9 11.01" />
    </svg>
  );
}

function SourcesIcon(): ReactElement {
  return (
    <svg {...iconProps()}>
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
      <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
    </svg>
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

// Studio stacks vertically (tablist on top, one panel below). Focus is the
// `.ws3-main` two-column split — Chat (flex) | Run-details panel — so the rail
// lays its children out in a row and drops the elevated fill (the Chat column
// takes the base `--ink` bg; the panel keeps the elevated `--ink2`).
const railStyle = (mode: RunMode): CSSProperties => ({
  display: "flex",
  flexDirection: mode === "focus" ? "row" : "column",
  height: "100%",
  minHeight: 0,
  minWidth: 0,
  overflow: "hidden",
  background:
    mode === "focus"
      ? "var(--color-bg, #0e1015)"
      : "var(--color-bg-elevated, #16181f)",
  color: "var(--color-text, #f4f5f6)",
  fontFamily: "var(--font-sans)",
});

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

// Chat panel wrapper. Studio: a stacked tab panel (visibility toggled). Focus:
// the always-visible LEFT column of the two-column split.
const chatPanelStyle = (mode: RunMode, visible: boolean): CSSProperties =>
  mode === "focus"
    ? {
        display: "flex",
        flexDirection: "column",
        flex: 1,
        minHeight: 0,
        minWidth: 0,
        overflow: "hidden",
      }
    : {
        display: visible ? "flex" : "none",
        flexDirection: "column",
        flex: 1,
        minHeight: 0,
        minWidth: 0,
        overflow: "auto",
      };

// Inner wrapper around `chatSlot` — present in BOTH modes (so the node's parent
// chain is stable across the Studio↔Focus switch). Focus constrains the content
// to the 730px centered column (`.fx-col`); Studio is a transparent pass-through.
const chatInnerStyle = (mode: RunMode): CSSProperties =>
  mode === "focus"
    ? {
        display: "flex",
        flexDirection: "column",
        flex: 1,
        minHeight: 0,
        minWidth: 0,
        width: "100%",
        maxWidth: 730,
        margin: "0 auto",
      }
    : {
        display: "flex",
        flexDirection: "column",
        flex: 1,
        minHeight: 0,
        minWidth: 0,
      };

// ── WS-F Focus Run-details panel (`.sd`, 324px) ──────────────────────────
const focusPanelStyle: CSSProperties = {
  flex: "none",
  width: 324,
  display: "flex",
  flexDirection: "column",
  minHeight: 0,
  minWidth: 0,
  overflow: "hidden",
  borderLeft: "1px solid var(--color-border, #22252e)",
  background: "var(--color-bg-elevated, #16181f)",
};

const focusPanelHeaderStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "9px 12px",
  borderBottom: "1px solid var(--color-border, #22252e)",
  flex: "none",
};

const focusPanelTitleStyle: CSSProperties = {
  flex: 1,
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  fontFamily: "var(--font-display, var(--font-sans))",
};

const focusTabsRowStyle: CSSProperties = {
  flex: "none",
  borderBottom: "1px solid var(--color-border, #22252e)",
};

const focusPanelBodyStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  flex: 1,
  minHeight: 0,
  minWidth: 0,
  overflow: "auto",
};

const focusIconButtonStyle: CSSProperties = {
  width: 24,
  height: 24,
  flex: "none",
  display: "grid",
  placeItems: "center",
  borderRadius: 6,
  border: 0,
  background: "transparent",
  color: "var(--color-text-muted, #9aa0a6)",
  cursor: "pointer",
};

// ── WS-F Focus Run-details icon rail (`.sd-strip`, 46px) ──────────────────
const focusStripStyle: CSSProperties = {
  flex: "none",
  width: 46,
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  gap: 4,
  padding: "9px 0",
  minWidth: 0,
  borderLeft: "1px solid var(--color-border, #22252e)",
  background: "var(--color-bg-elevated, #16181f)",
};

const focusStripButtonStyle: CSSProperties = {
  position: "relative",
  width: 32,
  height: 32,
  flex: "none",
  display: "grid",
  placeItems: "center",
  borderRadius: 8,
  border: 0,
  background: "transparent",
  color: "var(--color-text-muted, #9aa0a6)",
  cursor: "pointer",
};

const focusStripBadgeStyle: CSSProperties = {
  position: "absolute",
  top: 1,
  right: 1,
  minWidth: 13,
  height: 13,
  padding: "0 3px",
  borderRadius: 7,
  display: "grid",
  placeItems: "center",
  fontSize: 8.5,
  fontWeight: 700,
  fontFamily: "var(--font-mono, monospace)",
  color: "var(--color-accent-contrast, #101113)",
  background: "var(--color-accent, #5fb2ec)",
};

const approvalsBadgeStyle: CSSProperties = {
  color: "var(--color-accent, #5fb2ec)",
  fontWeight: 600,
};
