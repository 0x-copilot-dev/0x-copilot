// Right rail shell — chrome (header, toggle, frame) + optional tabs.
//
// Source: chats-canvas-prd.md §3.5 (binding 2026-05-17). When the
// destination is `chats` and a thread is active, the rail switches from
// the neutral empty-state into a two-tab view (Activity + Approvals).
// The shell stays the same in every destination; the host opts into the
// tabs by passing `activity` and `approvals` data.
//
// Decomposition:
//   - This file owns the chrome (frame, header, toggle, body slot).
//   - `<ActivityTabContent>` / `<ApprovalsTabContent>` own the content
//     panes (chronological feed + filtered approvals list with chips).
//   - The tab strip itself is `<FilterTabs>` — the Phase 0.5 ARIA
//     `tablist` primitive every destination uses for "All / Mentions /
//     Approvals"-style rows. Using it here keeps a single source of
//     truth for tab keyboard navigation, focus ring, and tokens.
//   - A separate `<RightRailTabs>` exists in this directory; it bundles
//     a tab strip + lighter content into one component. This file uses
//     the lower-level primitives directly so each pane gets the richer
//     `<EmptyState>` / `<StatusPill>` / `<FilterTabs>` chips treatment
//     the sub-PRD spells out for the live destinations wave.
//
// Backwards compatibility: when neither `activity` nor `approvals` is
// supplied AND no `children` are passed, the rail renders the same
// neutral empty-state as before so non-chats destinations are unchanged.

import type { CSSProperties, ReactElement, ReactNode } from "react";
import { useState } from "react";

// TODO(merge): rewire to "@0x-copilot/api-types" AssignedApproval
import type { Approval } from "../thread-canvas/_approvals-stub";
import type { ActivityEntry } from "../thread-canvas/eventProjector";

import { ActivityTabContent } from "./ActivityTabContent";
import { ApprovalsTabContent } from "./ApprovalsTabContent";
import { FilterTabs, type FilterTabOption } from "./FilterTabs";

const RAIL_WIDTH = 380;

export type RightRailTabId = "activity" | "approvals";

export interface RightRailProps {
  readonly open: boolean;
  readonly onToggle: () => void;
  /**
   * Optional header title — defaults to "Copilot conversation". Lets the
   * host re-label the right rail per destination without forking the
   * shell component.
   */
  readonly title?: string;
  /**
   * Optional content. When undefined AND no `activity`/`approvals` data
   * is supplied, a neutral empty-state is rendered so the rail never
   * shows hardcoded "Placeholder message" lines.
   *
   * When `children` IS supplied alongside the tab data, `children` wins —
   * hosts can fully override the body if they need something custom.
   */
  readonly children?: ReactNode;
  /**
   * Projector-produced activity entries. Pass `selectors.activityFeed`
   * from `eventProjector.ts` (or whatever the host exposes). When
   * provided alongside `approvals`, the body switches to the tabbed view.
   */
  readonly activity?: ReadonlyArray<ActivityEntry>;
  /**
   * Projector-produced approvals. Pass the values of `state.approvals`
   * from `eventProjector.ts`. Pending count drives the Approvals tab pill.
   */
  readonly approvals?: ReadonlyArray<Approval>;
  /**
   * Optional controlled active tab. When omitted the rail owns its tab
   * state internally (defaulting to "activity" per sub-PRD §3.5).
   */
  readonly activeTab?: RightRailTabId;
  readonly onTabChange?: (tab: RightRailTabId) => void;
  /**
   * Frozen `now` for tests; threads through to the content panes.
   */
  readonly now?: number;
}

export function RightRail({
  open,
  onToggle,
  title,
  children,
  activity,
  approvals,
  activeTab: controlledActiveTab,
  onTabChange,
  now,
}: RightRailProps): ReactElement {
  const headerLabel = title ?? "Copilot conversation";
  const [internalActiveTab, setInternalActiveTab] =
    useState<RightRailTabId>("activity");
  const activeTab = controlledActiveTab ?? internalActiveTab;

  const handleTabChange = (tab: RightRailTabId): void => {
    if (controlledActiveTab === undefined) {
      setInternalActiveTab(tab);
    }
    onTabChange?.(tab);
  };

  const containerStyle: CSSProperties = {
    width: open ? RAIL_WIDTH : 0,
    minWidth: open ? RAIL_WIDTH : 0,
    height: "100%",
    overflow: "hidden",
    position: "relative",
    backgroundColor: "var(--color-bg-elevated)",
    borderLeft: open ? "1px solid var(--color-border)" : "none",
    color: "var(--color-text)",
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    transition: "width 120ms ease, min-width 120ms ease",
  };
  const headerStyle: CSSProperties = {
    height: 44,
    minHeight: 44,
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "0 16px",
    borderBottom: "1px solid var(--color-border)",
    fontSize: "var(--font-size-sm)",
    fontWeight: 600,
    color: "var(--color-text)",
  };
  const bodyStyle: CSSProperties = {
    flex: 1,
    minHeight: 0,
    overflowY: "auto",
  };
  const toggleEdgeStyle: CSSProperties = {
    position: "absolute",
    // Centre the drawer handle on the right edge, out of the 44px topbar
    // band. Previously top:12 floated it into the top-right corner where it
    // collided with the topbar's right-hand control (the "tab overlaps
    // Studio" bug); mid-height keeps it clear of any topbar.
    top: "50%",
    transform: "translateY(-50%)",
    left: -28,
    width: 24,
    height: 24,
    background: "var(--color-bg-elevated)",
    border: "1px solid var(--color-border)",
    color: "var(--color-text)",
    borderRadius: 6,
    cursor: "pointer",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    padding: 0,
  };
  const toggleInsideStyle: CSSProperties = {
    background: "transparent",
    border: "none",
    color: "var(--color-text-muted)",
    cursor: "pointer",
    fontSize: "var(--font-size-sm)",
    padding: 0,
  };

  if (!open) {
    return (
      <aside
        aria-label={`${headerLabel} (collapsed)`}
        data-component="right-rail"
        data-state="closed"
        style={{ position: "relative", width: 0 }}
      >
        <button
          type="button"
          aria-label={`Open ${headerLabel}`}
          aria-expanded="false"
          data-testid="right-rail-toggle"
          onClick={onToggle}
          style={{ ...toggleEdgeStyle, left: -32 }}
        >
          {"<"}
        </button>
      </aside>
    );
  }

  // Tabs view is active when the host opts in by passing both tab data
  // arrays (even if one is empty — the empty case is handled by each
  // content pane's `<EmptyState>`). `children` overrides everything so
  // hosts can still drop in fully-custom bodies.
  const tabsEnabled =
    children === undefined && activity !== undefined && approvals !== undefined;

  const pendingCount = approvals
    ? approvals.reduce(
        (acc, approval) => (approval.state === "pending" ? acc + 1 : acc),
        0,
      )
    : 0;

  const tabOptions: ReadonlyArray<FilterTabOption<RightRailTabId>> = [
    { slug: "activity", label: "Activity" },
    {
      slug: "approvals",
      label: "Approvals",
      count: pendingCount > 0 ? pendingCount : undefined,
    },
  ];

  return (
    <aside
      aria-label={headerLabel}
      data-component="right-rail"
      data-state="open"
      style={containerStyle}
    >
      <button
        type="button"
        aria-label={`Close ${headerLabel}`}
        aria-expanded="true"
        data-testid="right-rail-toggle"
        onClick={onToggle}
        style={toggleEdgeStyle}
      >
        {">"}
      </button>
      <div style={headerStyle}>
        <span>{headerLabel}</span>
        <button
          type="button"
          aria-label={`${headerLabel} menu`}
          style={toggleInsideStyle}
        >
          ⋯
        </button>
      </div>
      <div style={bodyStyle} data-testid="right-rail-body">
        {children !== undefined ? (
          children
        ) : tabsEnabled ? (
          <>
            <FilterTabs<RightRailTabId>
              value={activeTab}
              onChange={handleTabChange}
              options={tabOptions}
              ariaLabel="Thread context"
              idPrefix="right-rail"
            />
            <div
              id={`right-rail-panel-${activeTab}`}
              role="tabpanel"
              aria-labelledby={`right-rail-tab-${activeTab}`}
              data-testid="right-rail-tabpanel"
              data-active-tab={activeTab}
              style={tabPanelStyle}
            >
              {activeTab === "activity" ? (
                <ActivityTabContent entries={activity ?? []} now={now} />
              ) : (
                <ApprovalsTabContent approvals={approvals ?? []} now={now} />
              )}
            </div>
          </>
        ) : (
          <EmptyStateMessage />
        )}
      </div>
    </aside>
  );
}

const tabPanelStyle: CSSProperties = {
  padding: "12px 16px",
};

function EmptyStateMessage(): ReactElement {
  return (
    <p
      style={{
        margin: 0,
        padding: "24px 16px",
        color: "var(--color-text-subtle)",
        fontSize: "var(--font-size-xs)",
        lineHeight: 1.55,
      }}
      data-testid="right-rail-empty"
    >
      Per-destination context surfaces here.
    </p>
  );
}

export { RAIL_WIDTH as RIGHT_RAIL_WIDTH };
