// <WhatsNewDigest> — collapsible past-tense activity since the user's
// last Home visit.
//
// Sub-PRD §3.1.4 + api-types/home.ts WhatsNewSection + HomeActivityRow.
//
// Rules:
//   - Empty data collapses the section entirely (returns null). Sub-PRD
//     §3.1 forbids per-section "Nothing here" placeholders.
//   - Header: `WHAT'S NEW · since {formatRelativeTime(since_iso)}`.
//   - Body is `<ActivityList>` (SP-1 primitive) over the section's
//     `HomeActivityRow[]`.
//   - Disclosure widget — host-controlled open/closed via `defaultOpen`
//     and the local toggle. Defaults to open.
//
// Error / unavailable: §3.1 says sections collapse if data is empty;
// for `error` status we still surface a small inline note so the user
// knows the digest exists but the data did not load. No retry CTA in
// the digest itself — global retry lives in the host data binder.

import { useState, type CSSProperties, type ReactElement } from "react";

import type {
  HomeActivityRow,
  WhatsNewSection,
} from "@enterprise-search/api-types";

import { ActivityList, type ActivityRow } from "../../../shell/ActivityList";
import { formatRelativeTime } from "../../../util/time";

export interface WhatsNewDigestProps {
  readonly section: WhatsNewSection;
  /** Frozen `now` for tests; defaults to `Date.now()` at render time. */
  readonly nowMs?: number;
  /** Whether the digest starts expanded. Defaults to true. */
  readonly defaultOpen?: boolean;
}

const sectionStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
};

const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  background: "transparent",
  border: "none",
  padding: 0,
  cursor: "pointer",
  color: "var(--color-text)",
  font: "inherit",
  textAlign: "left",
};

const headingStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  margin: 0,
  textTransform: "uppercase",
  letterSpacing: 0.4,
};

const sinceStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle)",
  fontWeight: 400,
  textTransform: "none",
  letterSpacing: 0,
  marginLeft: 8,
};

const errorBodyStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text-muted)",
  padding: "6px 10px",
};

function toActivityRow(row: HomeActivityRow): ActivityRow {
  // The api-types `HomeActivityRow` carries (kind, ref, title, summary?,
  // occurred_at). The shared `<ActivityList>` expects a stable `key` +
  // `ref` + ISO timestamp + optional context line. `key` falls back to a
  // composite of kind + occurred_at + ref.id (the row shape has no
  // top-level id field).
  return {
    key: `${row.kind}:${row.occurred_at}:${String(row.ref.id)}`,
    ref: row.ref,
    timestamp: row.occurred_at,
    context: row.summary,
  };
}

export function WhatsNewDigest({
  section,
  nowMs,
  defaultOpen = true,
}: WhatsNewDigestProps): ReactElement | null {
  const [isOpen, setIsOpen] = useState(defaultOpen);

  const rows = section.data ?? [];

  // Empty + ok → section collapses entirely (sub-PRD §3.1).
  if (section.status === "ok" && rows.length === 0) return null;
  // Unavailable → collapse (the digest is not load-bearing).
  if (section.status === "unavailable") return null;

  const since = formatRelativeTime(section.since_iso, nowMs);

  return (
    <section
      aria-labelledby="whats-new-heading"
      data-testid="home-whats-new"
      data-section-status={section.status}
      style={sectionStyle}
    >
      <button
        type="button"
        style={headerStyle}
        onClick={() => setIsOpen((v) => !v)}
        aria-expanded={isOpen}
        aria-controls="whats-new-body"
        data-testid="home-whats-new-toggle"
      >
        <h2 id="whats-new-heading" style={headingStyle}>
          What's new
          <span style={sinceStyle} data-testid="home-whats-new-since">
            since {since}
          </span>
        </h2>
        <span aria-hidden="true">{isOpen ? "▾" : "▸"}</span>
      </button>
      {isOpen ? (
        <div id="whats-new-body">
          {section.status === "error" ? (
            <div
              style={errorBodyStyle}
              role="status"
              data-testid="home-whats-new-error"
            >
              {section.error ?? "Couldn't load what's new."}
            </div>
          ) : (
            <ActivityList
              rows={rows.map(toActivityRow)}
              now={nowMs}
              ariaLabel="What's new since your last visit"
            />
          )}
        </div>
      ) : null}
    </section>
  );
}
