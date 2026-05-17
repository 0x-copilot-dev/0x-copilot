// <ApprovalsTabContent approvals={...} /> — right-rail Approvals content pane.
//
// Source: chats-canvas-prd.md §3.5 + §3.6 + §4.5 (binding 2026-05-17).
// The Approvals tab is a **summary** view of approvals for the active
// thread (per chat1.md L295 — the canonical Approve / Reject UI still
// lives inline in the surface). Each row in this tab is a navigable
// chip that scrolls the surface into view.
//
// Filter chips (All / Pending / Resolved) route through the shared
// `<FilterTabs>` primitive so the ARIA contract matches every other
// destination's filter row.
//
// Stub-import note: the `Approval` type comes from `_approvals-stub.ts`
// while P1-A's wire `approvals.ts` is in flight. The orchestrator rewires
// at merge time — grep for the explicit `TODO(merge)` comment below to
// find the single import site.

import {
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

// TODO(merge): rewire to "@enterprise-search/api-types" AssignedApproval
import type { Approval, ApprovalState } from "../thread-canvas/_approvals-stub";
import { ItemLink } from "../refs/ItemLink";
import { formatRelativeTime } from "../util/time";

import { EmptyState } from "./EmptyState";
import { FilterTabs, type FilterTabOption } from "./FilterTabs";
import { StatusPill, type StatusTone } from "./StatusPill";

export type ApprovalsFilter = "all" | "pending" | "resolved";

export interface ApprovalsTabContentProps {
  /**
   * Projector-produced approvals for the current conversation. Pass the
   * `Array.from(state.approvals.values())` slice from `eventProjector.ts`.
   */
  readonly approvals: ReadonlyArray<Approval>;
  /**
   * Frozen `now` for tests; defaults to `Date.now()` at render time.
   */
  readonly now?: number;
  /**
   * Optional controlled filter value. When omitted the component owns
   * its filter state internally (defaulting to "pending" per sub-PRD —
   * the rail surfaces what still needs action).
   */
  readonly filter?: ApprovalsFilter;
  readonly onFilterChange?: (filter: ApprovalsFilter) => void;
}

const RESOLVED_STATES: ReadonlySet<ApprovalState> = new Set([
  "accepted",
  "rejected",
  "edited",
]);

function isResolved(approval: Approval): boolean {
  return RESOLVED_STATES.has(approval.state);
}

function matchesFilter(approval: Approval, filter: ApprovalsFilter): boolean {
  if (filter === "all") return true;
  if (filter === "pending") return approval.state === "pending";
  return isResolved(approval);
}

function stateToTone(state: ApprovalState): StatusTone {
  switch (state) {
    case "pending":
      return "warning";
    case "accepted":
      return "ok";
    case "rejected":
      return "error";
    case "edited":
      return "info";
    default: {
      // Exhaustiveness — any new ApprovalState landing in api-types must
      // pick a tone explicitly (so we don't silently render "muted" and
      // hide a state from operators).
      const _exhaustive: never = state;
      return "muted";
    }
  }
}

function stateLabel(state: ApprovalState): string {
  switch (state) {
    case "pending":
      return "Pending";
    case "accepted":
      return "Accepted";
    case "rejected":
      return "Rejected";
    case "edited":
      return "Edited";
    default: {
      const _exhaustive: never = state;
      return state;
    }
  }
}

function emptyBody(filter: ApprovalsFilter): {
  readonly title: string;
  readonly body: string;
} {
  switch (filter) {
    case "pending":
      return {
        title: "No pending approvals",
        body: "Approvals awaiting your decision will appear here.",
      };
    case "resolved":
      return {
        title: "No resolved approvals",
        body: "Once approvals are accepted, rejected, or edited they will show up here.",
      };
    case "all":
    default:
      return {
        title: "No approvals yet",
        body: "Approvals for this thread will appear here.",
      };
  }
}

export function ApprovalsTabContent({
  approvals,
  now,
  filter: controlledFilter,
  onFilterChange,
}: ApprovalsTabContentProps): ReactElement {
  const [internalFilter, setInternalFilter] =
    useState<ApprovalsFilter>("pending");
  const filter = controlledFilter ?? internalFilter;

  const handleFilterChange = (next: ApprovalsFilter): void => {
    if (controlledFilter === undefined) {
      setInternalFilter(next);
    }
    onFilterChange?.(next);
  };

  // Pre-compute counts once per approvals list change so the chips reflect
  // the live totals without re-filtering on every render of every chip.
  const counts = useMemo(() => {
    let pending = 0;
    let resolved = 0;
    for (const approval of approvals) {
      if (approval.state === "pending") {
        pending += 1;
      } else if (isResolved(approval)) {
        resolved += 1;
      }
    }
    return {
      all: approvals.length,
      pending,
      resolved,
    } as const;
  }, [approvals]);

  const options: ReadonlyArray<FilterTabOption<ApprovalsFilter>> = [
    { slug: "all", label: "All", count: counts.all },
    { slug: "pending", label: "Pending", count: counts.pending },
    { slug: "resolved", label: "Resolved", count: counts.resolved },
  ];

  const filtered = useMemo(
    () =>
      approvals
        .filter((approval) => matchesFilter(approval, filter))
        .sort(
          // Pending first; within each bucket newest first (by created_at).
          (a, b) => b.created_at.localeCompare(a.created_at),
        ),
    [approvals, filter],
  );

  const empty = emptyBody(filter);

  return (
    <div data-testid="approvals-tab-content">
      <FilterTabs<ApprovalsFilter>
        value={filter}
        onChange={handleFilterChange}
        options={options}
        ariaLabel="Approvals filter"
        idPrefix="approvals-tab"
      />
      <div
        id={`approvals-tab-panel-${filter}`}
        role="tabpanel"
        aria-labelledby={`approvals-tab-tab-${filter}`}
        data-testid="approvals-tab-panel"
        data-active-filter={filter}
        style={panelStyle}
      >
        {filtered.length === 0 ? (
          <div data-testid="approvals-tab-empty" data-filter={filter}>
            <EmptyState title={empty.title} body={empty.body} />
          </div>
        ) : (
          <ul
            data-testid="approvals-tab-list"
            aria-label="Approvals"
            style={listStyle}
          >
            {filtered.map((approval) => (
              <li
                key={approval.id}
                data-testid={`approvals-tab-row-${approval.id}`}
                data-state={approval.state}
                style={rowStyle}
              >
                <div style={rowHeaderStyle}>
                  <ItemLink ref={{ kind: "approval", id: approval.id }} />
                  <StatusPill
                    status={stateToTone(approval.state)}
                    label={stateLabel(approval.state)}
                  />
                </div>
                <div style={rowMetaStyle}>
                  <span
                    data-testid={`approvals-tab-row-action-${approval.id}`}
                    style={actionStyle}
                  >
                    {approval.kind}
                  </span>
                  <span
                    data-testid={`approvals-tab-row-requester-${approval.id}`}
                    style={requesterStyle}
                  >
                    {approval.requester}
                  </span>
                  <time
                    dateTime={approval.created_at}
                    data-testid={`approvals-tab-row-time-${approval.id}`}
                    style={timestampStyle}
                  >
                    {formatRelativeTime(approval.created_at, now)}
                  </time>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

const panelStyle: CSSProperties = {
  paddingTop: 12,
};

const listStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  display: "flex",
  flexDirection: "column",
  gap: 8,
};

const rowStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  padding: "10px 12px",
  borderRadius: "var(--radius-sm, 8px)",
  background: "var(--color-surface-muted, #222224)",
  border: "1px solid var(--color-border, #232325)",
};

const rowHeaderStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
};

const rowMetaStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  flexWrap: "wrap",
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-muted, #b4b4b8)",
};

const actionStyle: CSSProperties = {
  fontWeight: 500,
  color: "var(--color-text, #ededee)",
};

const requesterStyle: CSSProperties = {
  color: "var(--color-text-subtle, #7e7e84)",
};

const timestampStyle: CSSProperties = {
  marginLeft: "auto",
  color: "var(--color-text-subtle, #7e7e84)",
};
