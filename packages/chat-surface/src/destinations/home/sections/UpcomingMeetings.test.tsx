import type { SectionResult } from "@enterprise-search/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  HOME_MEETINGS_NO_CONNECTOR,
  type HomeUpcomingMeeting,
} from "../_home-stub";
import { UpcomingMeetings } from "./UpcomingMeetings";

const NOW_MS = Date.parse("2026-05-18T12:00:00Z");

function makeMeeting(
  overrides: Partial<HomeUpcomingMeeting> = {},
): HomeUpcomingMeeting {
  return {
    meeting_id: "mtg_001",
    title: "Renewal review",
    starts_at: "2026-05-18T13:00:00Z",
    ends_at: "2026-05-18T13:30:00Z",
    attendee_count: 3,
    is_organizer: false,
    source_connector: "google_calendar",
    ...overrides,
  };
}

describe("<UpcomingMeetings>", () => {
  it("renders the 'Connect a calendar' CTA when status='unavailable' AND error sentinel matches", () => {
    const meetings: SectionResult<HomeUpcomingMeeting[]> = {
      status: "unavailable",
      error: HOME_MEETINGS_NO_CONNECTOR,
    };
    const onConnect = vi.fn();
    render(
      <UpcomingMeetings
        meetings={meetings}
        onConnectCalendar={onConnect}
        now={NOW_MS}
      />,
    );
    const cta = screen.getByTestId("home-upcoming-meetings-cta");
    expect(cta).toHaveAttribute("data-section-status", "unavailable");
    expect(cta).toHaveAttribute("data-cta-reason", "no-calendar-connector");
    expect(cta).toHaveTextContent(/Connect a calendar/i);
    const btn = screen.getByTestId("home-upcoming-meetings-cta-button");
    fireEvent.click(btn);
    expect(onConnect).toHaveBeenCalledTimes(1);
  });

  it("CTA click is a safe no-op when onConnectCalendar is not provided", () => {
    const meetings: SectionResult<HomeUpcomingMeeting[]> = {
      status: "unavailable",
      error: HOME_MEETINGS_NO_CONNECTOR,
    };
    render(<UpcomingMeetings meetings={meetings} />);
    const btn = screen.getByTestId("home-upcoming-meetings-cta-button");
    // Should not throw.
    expect(() => fireEvent.click(btn)).not.toThrow();
  });

  it("falls through to the generic 'unavailable' empty-state when error is NOT the no-connector sentinel", () => {
    const meetings: SectionResult<HomeUpcomingMeeting[]> = {
      status: "unavailable",
      error: "calendar_api_timeout",
    };
    render(<UpcomingMeetings meetings={meetings} />);
    // The CTA must NOT render for non-sentinel unavailable.
    expect(
      screen.queryByTestId("home-upcoming-meetings-cta"),
    ).not.toBeInTheDocument();
    const u = screen.getByTestId("home-upcoming-meetings-unavailable");
    expect(u).toHaveAttribute("data-section-status", "unavailable");
  });

  it("renders the empty state when status='ok' and data is empty", () => {
    const meetings: SectionResult<HomeUpcomingMeeting[]> = {
      status: "ok",
      data: [],
    };
    render(<UpcomingMeetings meetings={meetings} />);
    expect(screen.getByTestId("home-upcoming-meetings-empty")).toHaveAttribute(
      "data-section-status",
      "ok",
    );
    expect(screen.getByTestId("empty-state-title")).toHaveTextContent(
      "No meetings today.",
    );
  });

  it("renders one row per meeting with starts_at + connector chip when status='ok'", () => {
    const meetings: SectionResult<HomeUpcomingMeeting[]> = {
      status: "ok",
      data: [
        makeMeeting({
          meeting_id: "mtg_a",
          title: "Renewal review",
          source_connector: "google_calendar",
        }),
        makeMeeting({
          meeting_id: "mtg_b",
          title: "Standup",
          source_connector: "microsoft_calendar",
        }),
        makeMeeting({
          meeting_id: "mtg_c",
          title: "Other",
          source_connector: "other",
        }),
      ],
    };
    render(<UpcomingMeetings meetings={meetings} now={NOW_MS} />);
    const list = screen.getByTestId("home-upcoming-meetings");
    expect(list).toHaveAttribute("data-section-status", "ok");
    const rows = screen.getAllByTestId("home-upcoming-meeting-row");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveAttribute("data-meeting-id", "mtg_a");

    const chips = screen.getAllByTestId("home-upcoming-meeting-connector");
    expect(chips.map((c) => c.getAttribute("data-connector"))).toEqual([
      "google_calendar",
      "microsoft_calendar",
      "other",
    ]);
    expect(chips[0]).toHaveTextContent("Google Calendar");
    expect(chips[1]).toHaveTextContent("Microsoft Calendar");
    expect(chips[2]).toHaveTextContent("Calendar");

    // starts_at rendered as relative-time chip per row.
    expect(
      screen.getAllByTestId("home-upcoming-meeting-starts-at"),
    ).toHaveLength(3);
  });

  it("renders the error branch with role=alert", () => {
    const meetings: SectionResult<HomeUpcomingMeeting[]> = {
      status: "error",
      error: "connector_unhealthy",
    };
    render(<UpcomingMeetings meetings={meetings} />);
    const err = screen.getByTestId("home-upcoming-meetings-error");
    expect(err).toHaveAttribute("role", "alert");
    expect(screen.getByTestId("empty-state-body")).toHaveTextContent(
      "connector_unhealthy",
    );
  });
});
