// <RecentRuns> — P2-B3 home section.
//
// Pure presentation: renders a list of recent runs with a <StatusPill>
// per row. Branches on `SectionResult<HomeRecentRun[]>` status (ok /
// error / unavailable). Uses SP-1 primitives only (<DocList>,
// <StatusPill>, <EmptyState>) — no inline color choices, no per-row
// state besides what the link primitive owns.
//
// Source: docs/atlas-new-design/destinations/home-prd.md §4.2 +
// cross-audit.md §1.6 (status-pill is the single chip primitive).
//
// TODO(merge): _home-stub.ts is local; repoint to api-types when P2-A1
// merges (see _home-stub.ts header).

import type { CSSProperties, ReactElement } from "react";

import type { SectionResult } from "@enterprise-search/api-types";

import { DocList } from "../../../shell/DocList";
import { EmptyState } from "../../../shell/EmptyState";
import { ItemLink } from "../../../refs/ItemLink";
import { StatusPill, type StatusTone } from "../../../shell/StatusPill";
import { formatRelativeTime } from "../../../util/time";

import type { HomeRecentRun, HomeRecentRunStatus } from "../_home-stub";

export interface RecentRunsProps {
  readonly recent: SectionResult<HomeRecentRun[]>;
  /** Optional reference instant for relative time (test seam). */
  readonly now?: number;
}

const STATUS_TONE: Readonly<Record<HomeRecentRunStatus, StatusTone>> = {
  running: "info",
  queued: "info",
  succeeded: "ok",
  failed: "error",
  cancelled: "muted",
};

const STATUS_LABEL: Readonly<Record<HomeRecentRunStatus, string>> = {
  running: "Running",
  queued: "Queued",
  succeeded: "Succeeded",
  failed: "Failed",
  cancelled: "Cancelled",
};

const rowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  width: "100%",
};

const titleStyle: CSSProperties = {
  flex: 1,
  minWidth: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const metaStyle: CSSProperties = {
  flexShrink: 0,
  color: "var(--color-text-subtle, #7e7e84)",
  fontSize: "var(--font-size-xs, 12px)",
};

export function RecentRuns({ recent, now }: RecentRunsProps): ReactElement {
  if (recent.status === "error") {
    return (
      <div
        role="alert"
        data-testid="home-recent-runs-error"
        data-section-status="error"
      >
        <EmptyState
          title="Couldn't load recent runs"
          body={recent.error ?? "Try again in a moment."}
        />
      </div>
    );
  }

  if (recent.status === "unavailable") {
    return (
      <div
        data-testid="home-recent-runs-unavailable"
        data-section-status="unavailable"
      >
        <EmptyState
          title="Recent runs unavailable"
          body={recent.error ?? "This section is temporarily unavailable."}
        />
      </div>
    );
  }

  const runs = recent.data ?? [];
  if (runs.length === 0) {
    return (
      <div data-testid="home-recent-runs-empty" data-section-status="ok">
        <EmptyState title="No recent runs." />
      </div>
    );
  }

  return (
    <div data-testid="home-recent-runs" data-section-status="ok">
      <DocList<HomeRecentRun>
        ariaLabel="Recent runs"
        items={runs}
        keyFor={(run) => run.run_id}
        renderRow={(run) => (
          <div style={rowStyle} data-testid="home-recent-run-row">
            <span style={titleStyle}>
              <ItemLink ref={{ kind: "run", id: run.run_id }} />
            </span>
            <span style={titleStyle} data-testid="home-recent-run-title">
              {run.title}
            </span>
            <StatusPill
              status={STATUS_TONE[run.status]}
              label={STATUS_LABEL[run.status]}
            />
            <span style={metaStyle} data-testid="home-recent-run-time">
              {formatRelativeTime(run.started_at, now)}
            </span>
          </div>
        )}
      />
    </div>
  );
}
