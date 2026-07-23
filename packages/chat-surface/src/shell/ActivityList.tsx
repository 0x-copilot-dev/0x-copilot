// <ActivityList> — vertical list of activity rows.
//
// Source: destinations-master-prd §4.1. Used by Home (recent activity)
// and Routines later (next-fire feed). Each row: icon (optional) +
// <ItemLink ref={...}/> + timestamp + optional context line.
//
// The component owns the `<ul>/<li>` markup and spacing; consumers
// supply the per-row data. Time formatting flows through
// `util/time.ts` — destinations must NOT pass pre-formatted strings;
// pass the ISO timestamp and the row renders it.

import type { CSSProperties, ReactElement, ReactNode } from "react";

import type { ItemRef } from "@0x-copilot/api-types";

import { ItemLink } from "../refs/ItemLink";
import { itemKindNoun } from "../refs/itemKindNoun";
import { formatRelativeTime } from "../util/time";

export interface ActivityRow {
  /** Stable identity for React reconciliation. */
  readonly key: string;
  /** Optional icon to the left of the link. */
  readonly icon?: ReactNode;
  /** What the row points at; rendered as an <ItemLink>. */
  readonly ref: ItemRef;
  /** ISO-8601 timestamp; rendered via formatRelativeTime. */
  readonly timestamp: string;
  /** Optional secondary context line below the link (e.g. "in Acme renewal"). */
  readonly context?: string;
}

export interface ActivityListProps {
  readonly rows: ReadonlyArray<ActivityRow>;
  /** Frozen `now` for tests; defaults to `Date.now()` at render time. */
  readonly now?: number;
  readonly className?: string;
  readonly ariaLabel?: string;
}

const listStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
  margin: 0,
  padding: 0,
  listStyle: "none",
};

const rowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  padding: "8px 10px",
  borderRadius: "var(--radius-sm, 6px)",
  color: "var(--color-text, #ededee)",
};

const iconWrapStyle: CSSProperties = {
  display: "inline-flex",
  flexShrink: 0,
  color: "var(--color-text-subtle, #7e7e84)",
};

const linkBlockStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
  flex: 1,
  minWidth: 0,
};

const contextStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle, #7e7e84)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const timestampStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle, #7e7e84)",
  flexShrink: 0,
};

export function ActivityList({
  rows,
  now,
  className,
  ariaLabel,
}: ActivityListProps): ReactElement {
  return (
    <ul
      style={listStyle}
      className={className}
      aria-label={ariaLabel}
      data-testid="activity-list"
    >
      {rows.map((row) => (
        <li
          key={row.key}
          style={rowStyle}
          data-testid="activity-row"
          data-item-kind={row.ref.kind}
        >
          {row.icon !== undefined ? (
            <span aria-hidden="true" style={iconWrapStyle}>
              {row.icon}
            </span>
          ) : null}
          <div style={linkBlockStyle}>
            <ItemLink ref={row.ref} label={itemKindNoun(row.ref.kind)} />
            {row.context !== undefined && row.context.length > 0 ? (
              <div style={contextStyle} data-testid="activity-row-context">
                {row.context}
              </div>
            ) : null}
          </div>
          <time
            style={timestampStyle}
            dateTime={row.timestamp}
            data-testid="activity-row-timestamp"
          >
            {formatRelativeTime(row.timestamp, now)}
          </time>
        </li>
      ))}
    </ul>
  );
}
