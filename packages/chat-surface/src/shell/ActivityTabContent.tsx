// <ActivityTabContent entries={...} /> — right-rail Activity content pane.
//
// Source: chats-canvas-prd.md §3.5 + §4.2 (binding 2026-05-17). When the
// destination is `chats` and a thread is active, the right rail's
// **Activity** tab shows a chronological stream of the runtime events the
// projector flagged as visible.
//
// One projector, many consumers (sub-PRD §3.8). This component is a
// thin renderer over `ActivityEntry[]` produced by `eventProjector.ts` —
// it does NOT re-derive activity rows from raw `RuntimeEventEnvelope`s.
//
// Why we don't use `<ActivityList>` (Phase 0.5) here:
//   `<ActivityList>` requires every row to carry an `ItemRef` so it can
//   render via `<ItemLink>`. Thread-activity rows (think / stream / msg /
//   run-lifecycle) do not have a navigable artifact behind them — the
//   activity feed itself is informational. Rather than synthesize fake
//   `ItemRef`s, we render the rows directly using the same shell tokens
//   and structure as `<ActivityList>` (label + relative timestamp +
//   optional summary line). `<ApprovalsTabContent>` does use `<ItemLink>`
//   because every approval has a real `ApprovalId` to navigate to.
//
// Row ordering: reverse chronological (newest first). The projector
// emits append-only (oldest first); we sort a defensive copy.

import type { CSSProperties, ReactElement } from "react";

import type { ActivityEntry } from "../thread-canvas/eventProjector";
import { formatRelativeTime } from "../util/time";

import { EmptyState } from "./EmptyState";

export interface ActivityTabContentProps {
  /**
   * Projector-produced activity entries for the current conversation.
   * Pass `selectors.activityFeed(state)` from `eventProjector.ts`.
   */
  readonly entries: ReadonlyArray<ActivityEntry>;
  /**
   * Frozen `now` for tests; defaults to `Date.now()` at render time.
   */
  readonly now?: number;
}

function reverseChronological(
  entries: ReadonlyArray<ActivityEntry>,
): ReadonlyArray<ActivityEntry> {
  return [...entries].sort((a, b) => b.sequenceNo - a.sequenceNo);
}

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
  gap: 4,
  padding: "8px 10px",
  borderRadius: "var(--radius-sm, 6px)",
  background: "var(--color-surface-muted, #222224)",
  border: "1px solid var(--color-border, #232325)",
};

const rowHeaderStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
};

const kindBadgeStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  padding: "0 6px",
  height: 16,
  background: "var(--color-bg-elevated, #1a1a1c)",
  color: "var(--color-text-muted, #b4b4b8)",
  borderRadius: "var(--radius-xs, 4px)",
  fontSize: "var(--font-size-2xs, 11px)",
  textTransform: "uppercase",
  letterSpacing: 0.4,
  flexShrink: 0,
};

const titleStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text, #ededee)",
  fontWeight: 500,
  flex: 1,
  minWidth: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const timestampStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs, 11px)",
  color: "var(--color-text-subtle, #7e7e84)",
  flexShrink: 0,
};

const summaryStyle: CSSProperties = {
  margin: 0,
  color: "var(--color-text-muted, #b4b4b8)",
  fontSize: "var(--font-size-xs, 12px)",
  lineHeight: 1.5,
};

export function ActivityTabContent({
  entries,
  now,
}: ActivityTabContentProps): ReactElement {
  if (entries.length === 0) {
    return (
      <div data-testid="activity-tab-content" data-empty="true">
        <EmptyState
          title="No activity yet"
          body="Tool calls, approvals, and assistant replies for this thread will appear here as they happen."
        />
      </div>
    );
  }
  const ordered = reverseChronological(entries);
  return (
    <div data-testid="activity-tab-content" data-empty="false">
      <ul
        data-testid="activity-tab-list"
        aria-label="Thread activity"
        style={listStyle}
      >
        {ordered.map((entry) => (
          <li
            key={entry.id}
            data-testid={`activity-tab-row-${entry.id}`}
            data-kind={entry.kind}
            style={rowStyle}
          >
            <div style={rowHeaderStyle}>
              <span style={kindBadgeStyle}>{entry.kind}</span>
              <span style={titleStyle} title={entry.title}>
                {entry.title}
              </span>
              <time
                dateTime={entry.createdAt}
                style={timestampStyle}
                data-testid={`activity-tab-row-time-${entry.id}`}
              >
                {formatRelativeTime(entry.createdAt, now)}
              </time>
            </div>
            {entry.summary !== undefined && entry.summary.length > 0 ? (
              <p
                style={summaryStyle}
                data-testid={`activity-tab-row-summary-${entry.id}`}
              >
                {entry.summary}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
