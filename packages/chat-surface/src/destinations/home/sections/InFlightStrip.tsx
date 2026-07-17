// <InFlightStrip> — horizontal scroller of project cards (projects
// with activity in the last 7d).
//
// Sub-PRD §3.1.5 + api-types/home.ts InFlightProject.
//
// Card: project name + open_item_count badge + last_activity_at
// relative timestamp. Click target is `project.ref: ItemRef` so
// `<ItemLink>` resolves to the projects destination.
//
// Section collapses (returns null) if there are no in-flight projects.
// Overflow behavior: horizontal scroll — no pagination chrome. The
// host's container CSS bounds the visible width.

import type { CSSProperties, ReactElement } from "react";

import type { InFlightProject } from "@0x-copilot/api-types";

import { ItemLink } from "../../../refs/ItemLink";
import { StatusPill } from "../../../shell/StatusPill";
import { formatRelativeTime } from "../../../util/time";

export interface InFlightStripProps {
  readonly projects: ReadonlyArray<InFlightProject>;
  /** Frozen `now` for tests; defaults to `Date.now()` at render time. */
  readonly nowMs?: number;
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

const scrollerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "row",
  gap: 12,
  overflowX: "auto",
  paddingBottom: 4,
};

const cardStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  minWidth: 220,
  padding: "12px 14px",
  backgroundColor: "var(--color-surface)",
  border: "1px solid var(--color-border)",
  borderRadius: "var(--radius-md, 12px)",
  color: "var(--color-text)",
  flexShrink: 0,
};

const cardHeadStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
};

const emojiStyle: CSSProperties = {
  fontSize: 16,
  flexShrink: 0,
};

const nameStyle: CSSProperties = {
  flex: 1,
  minWidth: 0,
};

const metaStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle)",
};

export function InFlightStrip({
  projects,
  nowMs,
}: InFlightStripProps): ReactElement | null {
  if (projects.length === 0) return null;
  return (
    <section
      aria-labelledby="in-flight-heading"
      data-testid="home-in-flight"
      style={sectionStyle}
    >
      <h2 id="in-flight-heading" style={headingStyle}>
        In flight
      </h2>
      <div
        style={scrollerStyle}
        data-testid="home-in-flight-scroller"
        role="list"
        aria-label="Projects in flight"
      >
        {projects.map((project) => {
          const lastActivity = formatRelativeTime(
            project.last_activity_at,
            nowMs,
          );
          return (
            <article
              key={String(project.ref.id)}
              style={cardStyle}
              role="listitem"
              data-testid="home-in-flight-card"
              data-project-id={String(project.ref.id)}
            >
              <div style={cardHeadStyle}>
                <span aria-hidden="true" style={emojiStyle}>
                  {project.icon_emoji}
                </span>
                <div style={nameStyle}>
                  <ItemLink ref={project.ref} deletedLabel="deleted project" />
                </div>
              </div>
              <div style={metaStyle}>
                <StatusPill
                  status="info"
                  label={`${project.open_item_count} open`}
                />
                <time
                  dateTime={project.last_activity_at}
                  data-testid="home-in-flight-last-activity"
                >
                  {lastActivity}
                </time>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
