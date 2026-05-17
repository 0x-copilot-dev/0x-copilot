// <ActivityFeed> — Home's agent-activity section.
//
// Source: docs/atlas-new-design/destinations/home-prd.md §3.1.2 + §12.3 +
// §12.6. Wraps the shared `<ActivityList>` primitive — destinations do
// NOT roll their own list chrome.
//
// SectionResult branches (cross-audit §1.1 / api-types/refs.ts):
//
// - `status === "ok"`: render `<ActivityList>` with one row per entry.
//   Each row's `<ItemLink ref={row.target}>` does cross-destination
//   navigation (master §4.3 — no direct `router.navigate` from
//   sections).
// - `status === "error"`: render `<EmptyState>` with a Retry CTA (the
//   §12.6 partial-failure pattern). The retry callback is host-supplied
//   so this section stays pure-presentational.
// - `status === "unavailable"`: section is suppressed (returns null).
//   Used for not-yet-shipped backends or unsupported configurations;
//   the home destination still renders the rest of the page.
// - `status === "ok"` with zero rows: per-section empty state
//   (§12.3 copy) — Home should still show "Nothing's happened yet today."

import type { CSSProperties, ReactElement } from "react";

import type { SectionResult } from "@enterprise-search/api-types";

import { ActivityList, type ActivityRow } from "../../../shell/ActivityList";
import { EmptyState } from "../../../shell/EmptyState";
// TODO(merge): rewire to "@enterprise-search/api-types" once home.ts ships.
import type { HomeActivityRow } from "../_home-stub";

export interface ActivityFeedProps {
  readonly activity: SectionResult<ReadonlyArray<HomeActivityRow>>;
  /** Pin `now` for tests; defaults to `Date.now()` at render. */
  readonly nowMs?: number;
  /** Invoked when the user clicks the per-section retry CTA. */
  readonly onRetry?: () => void;
}

const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  marginBottom: 12,
};

const titleStyle: CSSProperties = {
  fontSize: "var(--font-size-lg, 16px)",
  fontWeight: 600,
  color: "var(--color-text, #ededee)",
  margin: 0,
};

const subtleStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle, #7e7e84)",
};

function toActivityRow(entry: HomeActivityRow): ActivityRow {
  return {
    key: entry.id,
    ref: entry.target,
    timestamp: entry.created_at,
    // Context line: "{agent name} — {backend-composed summary}". Both
    // strings come from the backend; the frontend does not template.
    context: `${entry.agent_name} — ${entry.summary}`,
  };
}

export function ActivityFeed({
  activity,
  nowMs,
  onRetry,
}: ActivityFeedProps): ReactElement | null {
  // Per home-prd §3.5 / cross-audit §1.1: an unavailable section is
  // suppressed entirely (vs error which surfaces a retry). Returning
  // null is intentional — Home renders the next section as if this one
  // were not in the layout.
  if (activity.status === "unavailable") {
    return null;
  }

  if (activity.status === "error") {
    return (
      <section
        data-testid="home-activity-feed"
        data-status="error"
        aria-label="Agent activity"
      >
        <header style={headerStyle}>
          <h2 style={titleStyle}>Agent activity</h2>
        </header>
        <EmptyState
          title="Couldn't load activity"
          body={
            activity.error !== undefined && activity.error.length > 0
              ? activity.error
              : "Other sections are unaffected. Try again in a moment."
          }
          action={
            onRetry !== undefined
              ? { label: "Retry", onClick: onRetry }
              : undefined
          }
        />
      </section>
    );
  }

  // status === "ok"
  const rows = activity.data ?? [];
  if (rows.length === 0) {
    return (
      <section
        data-testid="home-activity-feed"
        data-status="empty"
        aria-label="Agent activity"
      >
        <header style={headerStyle}>
          <h2 style={titleStyle}>Agent activity</h2>
        </header>
        <EmptyState
          title="Nothing's happened yet today."
          body="Atlas activity will appear here."
        />
      </section>
    );
  }

  return (
    <section
      data-testid="home-activity-feed"
      data-status="ok"
      aria-label="Agent activity"
    >
      <header style={headerStyle}>
        <h2 style={titleStyle}>Agent activity</h2>
        <span style={subtleStyle} data-testid="home-activity-count">
          {rows.length}
        </span>
      </header>
      <ActivityList
        rows={rows.map(toActivityRow)}
        now={nowMs}
        ariaLabel="Recent agent activity"
      />
    </section>
  );
}
