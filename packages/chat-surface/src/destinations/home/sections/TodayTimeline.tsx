// <TodayTimeline> — single chronological list of "things on my plate
// today" (meetings, routine fires, todos due today, scheduled runs).
//
// Sub-PRD §3.1.3 + api-types/home.ts TimelineEntry discriminated union.
//
// Rules:
//   - Sorted server-side by `when_iso`; this component does NOT
//     re-sort. It renders in payload order.
//   - Status decoration via <StatusPill> tone derived from
//     TimelineEntryStatus: in_progress/upcoming -> info; completed ->
//     muted; overdue/missed -> error.
//   - Click target is `entry.target: ItemRef` — rendered via
//     <ItemLink> (cross-audit §1.1). No raw routes.
//   - Cap at 8 visible entries; if more, render a "+N more today"
//     trailing row. The component is pure-presentation so the more-link
//     is just a textual hint — host wires deeper navigation.
//   - Section collapses (returns null) if `entries.length === 0`.
//
// ARIA: <section aria-labelledby="today-heading">; the heading carries
// the same id so screen readers pair them.

import type { CSSProperties, ReactElement } from "react";

import type { TimelineEntry } from "@0x-copilot/api-types";

import { ItemLink } from "../../../refs/ItemLink";
import { StatusPill, type StatusTone } from "../../../shell/StatusPill";

export interface TodayTimelineProps {
  readonly entries: ReadonlyArray<TimelineEntry>;
  /** Max rendered entries before the "+N more" trailing row. Default 8. */
  readonly cap?: number;
}

const TIMELINE_CAP_DEFAULT = 8;

function toneFor(status: TimelineEntry["status"]): StatusTone {
  switch (status) {
    case "completed":
      return "muted";
    case "in_progress":
      return "info";
    case "overdue":
    case "missed":
      return "error";
    case "upcoming":
      return "info";
  }
}

/** Discriminator dispatch: per-kind icon glyph + accessible label. */
function iconFor(entry: TimelineEntry): { glyph: string; label: string } {
  switch (entry.kind) {
    case "meeting":
      return { glyph: "🗓", label: "Meeting" };
    case "routine_fire":
      return { glyph: "⏱", label: "Routine fire" };
    case "todo_due":
      return { glyph: "✓", label: "Todo" };
    case "run_scheduled":
      return { glyph: "▶", label: "Run scheduled" };
  }
}

/** Display the "HH:MM" time chip from `when_iso`. UTC hours — the
 * server emits ISO UTC, and rendering in the caller's local zone would
 * make tests flaky across timezones and would mismatch the server's
 * tenant-local subtitle copy. Localization wraps server-side. */
function formatTimeChip(whenIso: string): string {
  const parsed = Date.parse(whenIso);
  if (Number.isNaN(parsed)) return "--:--";
  const d = new Date(parsed);
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

const sectionStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
};

const headingStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  color: "var(--color-text)",
  margin: 0,
  textTransform: "uppercase",
  letterSpacing: 0.4,
};

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
  color: "var(--color-text)",
};

const timeChipStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  minWidth: 56,
  height: 22,
  padding: "0 8px",
  borderRadius: "var(--radius-sm, 6px)",
  backgroundColor: "var(--color-surface-muted)",
  color: "var(--color-text-muted)",
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  flexShrink: 0,
};

const iconStyle: CSSProperties = {
  display: "inline-flex",
  flexShrink: 0,
  color: "var(--color-text-subtle)",
  width: 16,
};

const labelBlockStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
  flex: 1,
  minWidth: 0,
};

const subtitleStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const moreRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  padding: "8px 10px",
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle)",
};

export function TodayTimeline({
  entries,
  cap = TIMELINE_CAP_DEFAULT,
}: TodayTimelineProps): ReactElement | null {
  if (entries.length === 0) return null;
  const visible = entries.slice(0, cap);
  const overflow = Math.max(0, entries.length - cap);
  const headingId = "today-heading";
  return (
    <section
      aria-labelledby={headingId}
      data-testid="home-today-timeline"
      style={sectionStyle}
    >
      <h2 id={headingId} style={headingStyle}>
        Today
      </h2>
      <ul style={listStyle} aria-label="Today's timeline">
        {visible.map((entry) => {
          const icon = iconFor(entry);
          const tone = toneFor(entry.status);
          return (
            <li
              key={entry.id}
              style={rowStyle}
              data-testid="home-timeline-row"
              data-timeline-kind={entry.kind}
              data-timeline-status={entry.status}
            >
              <time
                style={timeChipStyle}
                dateTime={entry.when_iso}
                data-testid="home-timeline-time"
              >
                {formatTimeChip(entry.when_iso)}
              </time>
              <span
                aria-label={icon.label}
                style={iconStyle}
                data-testid="home-timeline-icon"
              >
                {icon.glyph}
              </span>
              <div style={labelBlockStyle}>
                <ItemLink ref={entry.target} label={entry.title} />
                {entry.subtitle !== undefined && entry.subtitle.length > 0 ? (
                  <div
                    style={subtitleStyle}
                    data-testid="home-timeline-subtitle"
                  >
                    {entry.subtitle}
                  </div>
                ) : null}
              </div>
              <StatusPill status={tone} label={entry.status} />
            </li>
          );
        })}
        {overflow > 0 ? (
          <li style={moreRowStyle} data-testid="home-timeline-more">
            {`+${overflow} more today`}
          </li>
        ) : null}
      </ul>
    </section>
  );
}
