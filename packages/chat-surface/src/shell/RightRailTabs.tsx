// Right rail tab strip — Activity + Approvals.
//
// Source: chats-canvas-prd.md §3.5 (binding 2026-05-17). When the active
// destination is `chats` AND a thread is active, the right rail switches
// from "empty / placeholder" to a two-tab view:
//
// - **Activity** (default) — chronological stream of think/MCP/tool/output
//   events; consumes `ActivityEntry[]` from the event projector.
// - **Approvals** — list of pending `Approval` entries; each row carries
//   Approve / Reject buttons (callbacks bubble up; the row itself is a
//   `<ItemLink ref={{kind:"approval", id}}>` so cross-destination
//   resolution still works.
//
// The tabs themselves are stateless. The owner (RightRail) controls the
// active-tab state so it can persist via KV and react to mode changes
// (Focus opens the rail with Activity selected).
//
// ARIA: `role="tablist"` + `role="tab"` per WAI-ARIA APG. Arrow-key
// navigation between tabs is the host's job; we render the chrome.

import {
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
} from "react";

import type { ApprovalId } from "@0x-copilot/api-types";

// TODO(merge): replace import from "../thread-canvas/_approvals-stub" with "@0x-copilot/api-types"
import type { Approval } from "../thread-canvas/_approvals-stub";
import type { ActivityEntry } from "../thread-canvas/eventProjector";
import { ItemLink } from "../refs/ItemLink";

export type RightRailTabId = "activity" | "approvals";

export interface RightRailTabsProps {
  readonly activeTab: RightRailTabId;
  readonly onTabChange: (tab: RightRailTabId) => void;
  readonly activity: readonly ActivityEntry[];
  readonly approvals: readonly Approval[];
  /**
   * Accept handler — called with the branded `ApprovalId`. Owner-only
   * write semantics are enforced server-side; we render the buttons
   * disabled when `canResolve === false` to give the UI hint.
   */
  readonly onAcceptApproval?: (id: ApprovalId) => void;
  readonly onRejectApproval?: (id: ApprovalId) => void;
  /**
   * False renders Approve/Reject as disabled with a tooltip
   * ("Only the thread owner can resolve this approval" per sub-PRD §7).
   */
  readonly canResolve?: boolean;
}

export function RightRailTabs(props: RightRailTabsProps): ReactElement {
  const {
    activeTab,
    onTabChange,
    activity,
    approvals,
    onAcceptApproval,
    onRejectApproval,
    canResolve = true,
  } = props;

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
      return;
    }
    event.preventDefault();
    onTabChange(activeTab === "activity" ? "approvals" : "activity");
  };

  return (
    <div data-testid="right-rail-tabs" style={containerStyle}>
      <div
        role="tablist"
        aria-label="Thread context"
        style={tabStripStyle}
        onKeyDown={handleKeyDown}
      >
        <TabButton
          id="activity"
          label="Activity"
          selected={activeTab === "activity"}
          onSelect={onTabChange}
        />
        <TabButton
          id="approvals"
          label="Approvals"
          count={approvals.length}
          selected={activeTab === "approvals"}
          onSelect={onTabChange}
        />
      </div>
      <div
        role="tabpanel"
        id="right-rail-tabpanel"
        aria-labelledby={`right-rail-tab-${activeTab}`}
        data-testid="right-rail-tabpanel"
        data-active-tab={activeTab}
        style={panelStyle}
      >
        {activeTab === "activity" ? (
          <ActivityPane entries={activity} />
        ) : (
          <ApprovalsPane
            approvals={approvals}
            canResolve={canResolve}
            onAccept={onAcceptApproval}
            onReject={onRejectApproval}
          />
        )}
      </div>
    </div>
  );
}

interface TabButtonProps {
  readonly id: RightRailTabId;
  readonly label: string;
  readonly count?: number;
  readonly selected: boolean;
  readonly onSelect: (tab: RightRailTabId) => void;
}

function TabButton(props: TabButtonProps): ReactElement {
  const { id, label, count, selected, onSelect } = props;
  return (
    <button
      type="button"
      role="tab"
      id={`right-rail-tab-${id}`}
      aria-selected={selected}
      aria-controls="right-rail-tabpanel"
      tabIndex={selected ? 0 : -1}
      data-testid={`right-rail-tab-${id}`}
      onClick={() => onSelect(id)}
      style={tabButtonStyle(selected)}
    >
      <span>{label}</span>
      {count !== undefined && count > 0 ? (
        <span data-testid={`right-rail-tab-count-${id}`} style={countPillStyle}>
          {count}
        </span>
      ) : null}
    </button>
  );
}

function ActivityPane(props: {
  readonly entries: readonly ActivityEntry[];
}): ReactElement {
  const { entries } = props;
  if (entries.length === 0) {
    return (
      <p
        data-testid="right-rail-activity-empty"
        role="status"
        style={emptyStyle}
      >
        No activity yet.
      </p>
    );
  }
  return (
    <ul data-testid="right-rail-activity-list" style={ulStyle}>
      {entries.map((entry) => (
        <li
          key={entry.id}
          data-testid={`right-rail-activity-row-${entry.id}`}
          data-kind={entry.kind}
          style={activityRowStyle}
        >
          <div style={activityHeaderStyle}>
            <span style={activityKindBadgeStyle}>{entry.kind}</span>
            <span style={activityTitleStyle}>{entry.title}</span>
          </div>
          {entry.summary !== undefined ? (
            <p style={activitySummaryStyle}>{entry.summary}</p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

interface ApprovalsPaneProps {
  readonly approvals: readonly Approval[];
  readonly canResolve: boolean;
  readonly onAccept: ((id: ApprovalId) => void) | undefined;
  readonly onReject: ((id: ApprovalId) => void) | undefined;
}

function ApprovalsPane(props: ApprovalsPaneProps): ReactElement {
  const { approvals, canResolve, onAccept, onReject } = props;
  if (approvals.length === 0) {
    return (
      <p
        data-testid="right-rail-approvals-empty"
        role="status"
        style={emptyStyle}
      >
        No pending approvals.
      </p>
    );
  }
  const disabledTip = canResolve
    ? undefined
    : "Only the thread owner can resolve this approval";
  return (
    <ul data-testid="right-rail-approvals-list" style={ulStyle}>
      {approvals.map((approval) => (
        <li
          key={approval.id}
          data-testid={`right-rail-approval-row-${approval.id}`}
          style={approvalRowStyle}
        >
          <div style={approvalHeaderStyle}>
            <ItemLink ref={{ kind: "approval", id: approval.id }} />
            <span data-testid={`right-rail-approval-kind-${approval.id}`}>
              {approval.kind}
            </span>
          </div>
          <div
            role="group"
            aria-label="Resolve approval"
            style={actionsRowStyle}
          >
            <button
              type="button"
              data-testid={`right-rail-approval-reject-${approval.id}`}
              onClick={() => onReject?.(approval.id)}
              disabled={!canResolve}
              title={disabledTip}
              style={rejectButtonStyle(canResolve)}
            >
              Reject
            </button>
            <button
              type="button"
              data-testid={`right-rail-approval-accept-${approval.id}`}
              onClick={() => onAccept?.(approval.id)}
              disabled={!canResolve}
              title={disabledTip}
              style={acceptButtonStyle(canResolve)}
            >
              Accept
            </button>
          </div>
        </li>
      ))}
    </ul>
  );
}

const containerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  height: "100%",
  minHeight: 0,
};

const tabStripStyle: CSSProperties = {
  display: "flex",
  gap: 0,
  borderBottom: "1px solid var(--color-border)",
  padding: "0 8px",
};

const tabButtonStyle = (selected: boolean): CSSProperties => ({
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  background: "transparent",
  border: "none",
  borderBottom: `2px solid ${selected ? "var(--color-accent)" : "transparent"}`,
  color: selected ? "var(--color-text)" : "var(--color-text-muted)",
  padding: "8px 12px",
  fontSize: "var(--font-size-sm)",
  cursor: "pointer",
  fontFamily: "inherit",
});

const countPillStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  minWidth: 18,
  height: 18,
  padding: "0 5px",
  borderRadius: 999,
  background: "var(--color-surface-muted)",
  color: "var(--color-text-muted)",
  fontSize: "var(--font-size-2xs)",
  fontWeight: 600,
};

const panelStyle: CSSProperties = {
  flex: 1,
  minHeight: 0,
  overflowY: "auto",
  padding: "12px 16px",
  color: "var(--color-text)",
  fontSize: "var(--font-size-sm)",
};

const emptyStyle: CSSProperties = {
  margin: 0,
  color: "var(--color-text-subtle)",
  fontSize: "var(--font-size-xs)",
  lineHeight: 1.55,
};

const ulStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  display: "flex",
  flexDirection: "column",
  gap: 10,
};

const activityRowStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
  padding: "8px 10px",
  borderRadius: 6,
  background: "var(--color-surface-muted)",
  border: "1px solid var(--color-border)",
};

const activityHeaderStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
};

const activityKindBadgeStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  padding: "0 6px",
  height: 16,
  background: "var(--color-bg-elevated)",
  color: "var(--color-text-muted)",
  borderRadius: 4,
  fontSize: "var(--font-size-2xs)",
  textTransform: "uppercase",
  letterSpacing: 0.4,
};

const activityTitleStyle: CSSProperties = {
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text)",
  fontWeight: 500,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const activitySummaryStyle: CSSProperties = {
  margin: 0,
  color: "var(--color-text-muted)",
  fontSize: "var(--font-size-2xs)",
  lineHeight: 1.5,
};

const approvalRowStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  padding: "10px 12px",
  borderRadius: 8,
  background: "var(--color-surface-muted)",
  border: "1px solid var(--color-border)",
};

const approvalHeaderStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text-muted)",
};

const actionsRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "flex-end",
  gap: 6,
};

const acceptButtonStyle = (enabled: boolean): CSSProperties => ({
  background: enabled ? "var(--color-accent)" : "var(--color-surface-muted)",
  color: enabled ? "var(--color-accent-contrast)" : "var(--color-text-subtle)",
  border: "none",
  borderRadius: 6,
  padding: "4px 10px",
  fontSize: "var(--font-size-2xs)",
  fontWeight: 600,
  cursor: enabled ? "pointer" : "not-allowed",
});

const rejectButtonStyle = (enabled: boolean): CSSProperties => ({
  background: "transparent",
  color: enabled ? "var(--color-text)" : "var(--color-text-subtle)",
  border: "1px solid var(--color-border)",
  borderRadius: 6,
  padding: "4px 10px",
  fontSize: "var(--font-size-2xs)",
  cursor: enabled ? "pointer" : "not-allowed",
});
