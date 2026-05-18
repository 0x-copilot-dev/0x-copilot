// <LiveActivityRail> — subordinate right-rail "background hum" feed.
//
// Sub-PRD §3.1.6. The rail is intentionally low-contrast — TriageStrip
// and WhatsNewDigest carry the primary signal. The rail renders the
// most-recent N rows; older entries scroll off the cap.
//
// Pure-presentation: SSE merge + reconnect live in the host data
// binder (P9-C). This component just renders whatever rows the host
// hands it. Each row is a `HomeActivityRow` (api-types), wired through
// the shared `<ActivityList>` primitive.

import type { CSSProperties, ReactElement } from "react";

import type { HomeActivityRow } from "@enterprise-search/api-types";

import { ActivityList, type ActivityRow } from "../../../shell/ActivityList";

export interface LiveActivityRailProps {
  readonly rows: ReadonlyArray<HomeActivityRow>;
  /** Soft cap on rendered rows. Default 15 (sub-PRD §3.1.6). */
  readonly cap?: number;
  /** Frozen `now` for tests; defaults to `Date.now()` at render time. */
  readonly nowMs?: number;
}

const LIVE_RAIL_CAP_DEFAULT = 15;

const railStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

const headingStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  color: "var(--color-text-subtle)",
  margin: 0,
  textTransform: "uppercase",
  letterSpacing: 0.4,
};

const emptyStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle)",
  padding: "4px 8px",
};

function toActivityRow(row: HomeActivityRow): ActivityRow {
  return {
    key: `${row.kind}:${row.occurred_at}:${String(row.ref.id)}`,
    ref: row.ref,
    timestamp: row.occurred_at,
    context: row.summary,
  };
}

export function LiveActivityRail({
  rows,
  cap = LIVE_RAIL_CAP_DEFAULT,
  nowMs,
}: LiveActivityRailProps): ReactElement {
  const visible = rows.slice(0, cap);
  return (
    <aside
      aria-label="Live activity"
      data-testid="home-live-activity-rail"
      style={railStyle}
    >
      <h3 style={headingStyle}>Live activity</h3>
      {visible.length === 0 ? (
        <div style={emptyStyle} data-testid="home-live-activity-empty">
          Nothing recent.
        </div>
      ) : (
        <ActivityList
          rows={visible.map(toActivityRow)}
          now={nowMs}
          ariaLabel="Live agent activity"
        />
      )}
    </aside>
  );
}
