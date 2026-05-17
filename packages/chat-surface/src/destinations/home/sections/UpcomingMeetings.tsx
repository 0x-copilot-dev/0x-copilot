// <UpcomingMeetings> — P2-B3 home section.
//
// Pure presentation with one special-case branch: when no calendar
// connector is connected, the section returns
//   { status: "unavailable", error: HOME_MEETINGS_NO_CONNECTOR }
// and the UI renders a "Connect a calendar to see today's meetings →"
// CTA row instead of a generic empty-state. Click invokes the optional
// `onConnectCalendar` callback (P2-B1 wires this to Connectors
// destination navigation).
//
// All other unavailable cases (transient connector outage, downstream
// timeout) fall through to the standard "unavailable" empty-state.
//
// Source: docs/atlas-new-design/destinations/home-prd.md §4.5 + §13
// (no-calendar CTA) + cross-audit.md §1.1.
//
// TODO(merge): _home-stub.ts is local; repoint to api-types when P2-A1
// merges. The no-connector sentinel string MUST be kept in sync with
// the backend composer (P2-A1 owns).

import type { CSSProperties, ReactElement } from "react";

import type { SectionResult } from "@enterprise-search/api-types";

import { EmptyState } from "../../../shell/EmptyState";
import { formatRelativeTime } from "../../../util/time";

import {
  HOME_MEETINGS_NO_CONNECTOR,
  type HomeMeetingConnectorKind,
  type HomeUpcomingMeeting,
} from "../_home-stub";

export interface UpcomingMeetingsProps {
  readonly meetings: SectionResult<HomeUpcomingMeeting[]>;
  /**
   * Optional click-target for the "Connect a calendar" CTA row. When
   * omitted the CTA still renders but click is a no-op (so the section
   * works in isolation / Storybook). P2-B1 wires this to navigate to
   * the Connectors destination.
   */
  readonly onConnectCalendar?: () => void;
  /** Optional reference instant for relative time (test seam). */
  readonly now?: number;
}

const CONNECTOR_LABEL: Readonly<Record<HomeMeetingConnectorKind, string>> = {
  google_calendar: "Google Calendar",
  microsoft_calendar: "Microsoft Calendar",
  other: "Calendar",
};

const ctaWrapperStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  padding: "12px 14px",
  borderRadius: "var(--radius-md, 12px)",
  border: "1px dashed var(--color-border-strong, #2a2a2c)",
  backgroundColor: "var(--color-bg-elevated, #161617)",
  color: "var(--color-text, #ededee)",
};

const ctaButtonStyle: CSSProperties = {
  appearance: "none",
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  height: 28,
  padding: "0 12px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border-strong, #2a2a2c)",
  backgroundColor: "transparent",
  color: "var(--color-accent, #d97757)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
};

const listStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
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
  border: "1px solid var(--color-border, #232325)",
  backgroundColor: "var(--color-bg-elevated, #161617)",
};

const titleStyle: CSSProperties = {
  flex: 1,
  minWidth: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-sm, 13px)",
};

const startsAtStyle: CSSProperties = {
  flexShrink: 0,
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle, #7e7e84)",
};

const chipStyle: CSSProperties = {
  flexShrink: 0,
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
  padding: "2px 8px",
  borderRadius: "var(--radius-full, 999px)",
  border: "1px solid var(--color-border, #232325)",
  backgroundColor: "var(--color-surface-muted, #222224)",
  color: "var(--color-text-muted, #b4b4b8)",
  fontSize: "var(--font-size-2xs, 11px)",
};

function isNoConnectorError(
  meetings: SectionResult<HomeUpcomingMeeting[]>,
): boolean {
  return (
    meetings.status === "unavailable" &&
    meetings.error === HOME_MEETINGS_NO_CONNECTOR
  );
}

export function UpcomingMeetings({
  meetings,
  onConnectCalendar,
  now,
}: UpcomingMeetingsProps): ReactElement {
  // No-connector CTA — the §13 product decision: replace the section
  // body entirely with a single CTA row, not a generic empty-state.
  if (isNoConnectorError(meetings)) {
    return (
      <div
        style={ctaWrapperStyle}
        data-testid="home-upcoming-meetings-cta"
        data-section-status="unavailable"
        data-cta-reason="no-calendar-connector"
      >
        <span>Connect a calendar to see today's meetings</span>
        <button
          type="button"
          style={ctaButtonStyle}
          onClick={onConnectCalendar}
          data-testid="home-upcoming-meetings-cta-button"
        >
          Connect a calendar →
        </button>
      </div>
    );
  }

  if (meetings.status === "error") {
    return (
      <div
        role="alert"
        data-testid="home-upcoming-meetings-error"
        data-section-status="error"
      >
        <EmptyState
          title="Couldn't load meetings"
          body={meetings.error ?? "Try again in a moment."}
        />
      </div>
    );
  }

  if (meetings.status === "unavailable") {
    return (
      <div
        data-testid="home-upcoming-meetings-unavailable"
        data-section-status="unavailable"
      >
        <EmptyState
          title="Meetings unavailable"
          body={meetings.error ?? "This section is temporarily unavailable."}
        />
      </div>
    );
  }

  const items = meetings.data ?? [];
  if (items.length === 0) {
    return (
      <div data-testid="home-upcoming-meetings-empty" data-section-status="ok">
        <EmptyState title="No meetings today." />
      </div>
    );
  }

  return (
    <ul
      style={listStyle}
      data-testid="home-upcoming-meetings"
      data-section-status="ok"
      aria-label="Upcoming meetings"
    >
      {items.map((meeting) => (
        <li
          key={meeting.meeting_id}
          style={rowStyle}
          data-testid="home-upcoming-meeting-row"
          data-meeting-id={meeting.meeting_id}
        >
          <span style={titleStyle} data-testid="home-upcoming-meeting-title">
            {meeting.title}
          </span>
          <span
            style={startsAtStyle}
            data-testid="home-upcoming-meeting-starts-at"
          >
            {formatRelativeTime(meeting.starts_at, now)}
          </span>
          <span
            style={chipStyle}
            data-testid="home-upcoming-meeting-connector"
            data-connector={meeting.source_connector}
          >
            {CONNECTOR_LABEL[meeting.source_connector]}
          </span>
        </li>
      ))}
    </ul>
  );
}
