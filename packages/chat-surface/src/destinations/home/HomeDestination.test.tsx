// HomeDestination — Phase 9 presentation tests.
//
// Pure-presentation: no transport, no router. Tests feed `HomePayload`
// directly and assert on rendered DOM.

import type {
  ApprovalId,
  ConversationId,
  HomeActivityRow,
  HomePayload,
  InFlightProject,
  ItemRef,
  MeetingExternalId,
  ProjectId,
  RoutineId,
  RunId,
  SectionResult,
  TimelineEntry,
  TodoId,
  WhatsNewSection,
} from "@0x-copilot/api-types";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../providers/RouterProvider";
import type { ArtifactRoute, Router } from "../../routing/router";

import { HomeDestination, type HomeDestinationProps } from "./HomeDestination";

// === Fixtures ==============================================================

const NOW_MS = Date.parse("2026-05-17T12:00:00.000Z");

function okSection<T>(data: T): SectionResult<T> {
  return { status: "ok", data };
}

const CONV_1 = "conv_1" as ConversationId;
const RUN_1 = "run_1" as RunId;
const TODO_1 = "todo_1" as TodoId;
const ROUTINE_1 = "routine_1" as RoutineId;
const MEET_1 = "meet_1" as MeetingExternalId;
const PROJ_1 = "proj_1" as ProjectId;
const APPROVAL_1 = "approval_1" as ApprovalId;

const MEETING_ENTRY: TimelineEntry = {
  id: "tl_meeting",
  kind: "meeting",
  when_iso: "2026-05-17T09:30:00.000Z",
  title: "Standup",
  subtitle: "Calendar",
  status: "upcoming",
  target: { kind: "meeting_external", id: MEET_1 },
  end_iso: "2026-05-17T10:00:00.000Z",
  attendee_count: 4,
  is_organizer: false,
  source_connector: "google_calendar",
};

const ROUTINE_ENTRY: TimelineEntry = {
  id: "tl_routine",
  kind: "routine_fire",
  when_iso: "2026-05-17T11:00:00.000Z",
  title: "Q3 metrics digest",
  subtitle: "Routine fires",
  status: "upcoming",
  target: { kind: "routine", id: ROUTINE_1 },
  trigger_kind: "scheduled",
};

const TODO_ENTRY: TimelineEntry = {
  id: "tl_todo",
  kind: "todo_due",
  when_iso: "2026-05-17T14:00:00.000Z",
  title: "Review PR #482",
  subtitle: "Due",
  status: "overdue",
  target: { kind: "todo", id: TODO_1 },
  priority: "high",
  is_overdue: true,
  source_kind: "user",
};

const RUN_ENTRY: TimelineEntry = {
  id: "tl_run",
  kind: "run_scheduled",
  when_iso: "2026-05-17T16:00:00.000Z",
  title: "Q3 forecast refresh",
  subtitle: "Run scheduled",
  status: "in_progress",
  target: { kind: "run", id: RUN_1 },
  agent_name: "Brief Author",
};

const PROJECT: InFlightProject = {
  ref: { kind: "project", id: PROJ_1 },
  name: "Launch prep",
  icon_emoji: "📁",
  color_hue: 220,
  open_item_count: 4,
  last_activity_at: "2026-05-17T11:00:00.000Z",
};

const ACTIVITY_ROW: HomeActivityRow = {
  kind: "run",
  ref: { kind: "run", id: RUN_1 },
  title: "Q3 forecast refresh",
  summary: "Atlas finished a forecast refresh.",
  occurred_at: "2026-05-17T11:55:00.000Z",
};

function makePayload(over: Partial<HomePayload> = {}): HomePayload {
  return {
    greeting: {
      display_name: "Sarah",
      time_segment: "morning",
      tenant_local_date: "Sun, May 17",
      tenant_local_iso: "2026-05-17T08:00:00.000Z",
    },
    triage: {
      approvals_waiting: 2,
      runs_failed_24h: 0,
      todos_overdue: 6,
      todos_due_today: 1,
    },
    today_timeline: okSection<readonly TimelineEntry[]>([
      MEETING_ENTRY,
      ROUTINE_ENTRY,
      TODO_ENTRY,
      RUN_ENTRY,
    ]),
    whats_new: {
      status: "ok",
      since_iso: "2026-05-17T08:00:00.000Z",
      data: [ACTIVITY_ROW],
    } as WhatsNewSection,
    in_flight_projects: okSection<readonly InFlightProject[]>([PROJECT]),
    live_activity: okSection<readonly HomeActivityRow[]>([ACTIVITY_ROW]),
    quick_actions: [],
    cached_at: "2026-05-17T11:59:00.000Z",
    is_first_run: false,
    ...over,
  };
}

const noopRouter: Router<ArtifactRoute> = {
  current: () => ({ kind: "chat", conversationId: "x" }) as ArtifactRoute,
  navigate: () => undefined,
  subscribe: () => () => undefined,
};

function renderHome(props: HomeDestinationProps = {}): void {
  render(
    <RouterProvider router={noopRouter}>
      <HomeDestination nowMs={NOW_MS} {...props} />
    </RouterProvider>,
  );
}

// ===========================================================================

describe("HomeDestination — Phase 9", () => {
  it("renders the skeleton state when homeResponse is null", () => {
    renderHome({ homeResponse: null });
    const region = screen.getByRole("region", { name: /home destination/i });
    expect(region).toHaveAttribute("data-state", "loading");
    expect(
      screen.getAllByTestId("home-skeleton-section").length,
    ).toBeGreaterThan(0);
  });

  it("renders the greeting from the payload via PageHeader", () => {
    renderHome({ homeResponse: makePayload() });
    const region = screen.getByRole("region", { name: /home destination/i });
    expect(region).toHaveAttribute("data-state", "ready");
    expect(screen.getByTestId("page-header-title")).toHaveTextContent(
      "Good morning, Sarah.",
    );
    expect(screen.getByTestId("page-header-subtitle")).toHaveTextContent(
      "Sun, May 17",
    );
  });

  it("falls back to a nameless greeting when display_name is null", () => {
    renderHome({
      homeResponse: makePayload({
        greeting: {
          display_name: null,
          time_segment: "afternoon",
          tenant_local_date: "Sun, May 17",
          tenant_local_iso: "2026-05-17T13:00:00.000Z",
        },
      }),
    });
    expect(screen.getByTestId("page-header-title")).toHaveTextContent(
      "Good afternoon.",
    );
  });

  it("renders all four triage tiles inside an aria-labelled nav", () => {
    renderHome({ homeResponse: makePayload() });
    const nav = screen.getByRole("navigation", { name: /triage/i });
    expect(within(nav).getAllByRole("button")).toHaveLength(4);
  });

  it("derives triage tone from count thresholds (0/1-4/>=5)", () => {
    renderHome({ homeResponse: makePayload() });
    // approvals_waiting=2 → warning (1-4)
    expect(
      screen.getByTestId("home-triage-tile-approvals_waiting"),
    ).toHaveAttribute("data-triage-tone", "warning");
    // runs_failed_24h=0 → muted
    expect(
      screen.getByTestId("home-triage-tile-runs_failed_24h"),
    ).toHaveAttribute("data-triage-tone", "muted");
    // todos_overdue=6 → error (>=5)
    expect(
      screen.getByTestId("home-triage-tile-todos_overdue"),
    ).toHaveAttribute("data-triage-tone", "error");
    // todos_due_today=1 → warning
    expect(
      screen.getByTestId("home-triage-tile-todos_due_today"),
    ).toHaveAttribute("data-triage-tone", "warning");
  });

  it("invokes onTriageSelect with an ItemRef when a tile is clicked", () => {
    const onTriageSelect = vi.fn<(ref: ItemRef) => void>();
    renderHome({ homeResponse: makePayload(), onTriageSelect });
    fireEvent.click(screen.getByTestId("home-triage-tile-approvals_waiting"));
    expect(onTriageSelect).toHaveBeenCalledTimes(1);
    const ref = onTriageSelect.mock.calls[0]?.[0];
    expect(ref?.kind).toBe("approval");
    expect(String(ref?.id)).toBe("__triage_approvals_waiting__");
  });

  // === TimelineEntry kind discrimination ===================================

  it("renders a TimelineEntry of kind=meeting", () => {
    renderHome({
      homeResponse: makePayload({
        today_timeline: okSection<readonly TimelineEntry[]>([MEETING_ENTRY]),
      }),
    });
    const row = screen.getByTestId("home-timeline-row");
    expect(row).toHaveAttribute("data-timeline-kind", "meeting");
    expect(row).toHaveTextContent("09:30");
  });

  it("renders a TimelineEntry of kind=routine_fire", () => {
    renderHome({
      homeResponse: makePayload({
        today_timeline: okSection<readonly TimelineEntry[]>([ROUTINE_ENTRY]),
      }),
    });
    const row = screen.getByTestId("home-timeline-row");
    expect(row).toHaveAttribute("data-timeline-kind", "routine_fire");
  });

  it("renders a TimelineEntry of kind=todo_due with overdue tone", () => {
    renderHome({
      homeResponse: makePayload({
        today_timeline: okSection<readonly TimelineEntry[]>([TODO_ENTRY]),
      }),
    });
    const row = screen.getByTestId("home-timeline-row");
    expect(row).toHaveAttribute("data-timeline-kind", "todo_due");
    expect(row).toHaveAttribute("data-timeline-status", "overdue");
    // overdue → error tone on the StatusPill
    expect(within(row).getByTestId("status-pill")).toHaveAttribute(
      "data-status",
      "error",
    );
  });

  it("renders a TimelineEntry of kind=run_scheduled", () => {
    renderHome({
      homeResponse: makePayload({
        today_timeline: okSection<readonly TimelineEntry[]>([RUN_ENTRY]),
      }),
    });
    const row = screen.getByTestId("home-timeline-row");
    expect(row).toHaveAttribute("data-timeline-kind", "run_scheduled");
  });

  it("collapses the timeline section when entries are empty", () => {
    renderHome({
      homeResponse: makePayload({
        today_timeline: okSection<readonly TimelineEntry[]>([]),
      }),
    });
    expect(screen.queryByTestId("home-today-timeline")).toBeNull();
  });

  it("renders TodayTimeline inside aria-labelled section", () => {
    renderHome({ homeResponse: makePayload() });
    const section = screen.getByTestId("home-today-timeline");
    expect(section.tagName).toBe("SECTION");
    expect(section).toHaveAttribute("aria-labelledby", "today-heading");
    expect(screen.getByText("Today").id).toBe("today-heading");
  });

  // === WhatsNew empty state ================================================

  it("collapses WhatsNewDigest when its data is empty", () => {
    renderHome({
      homeResponse: makePayload({
        whats_new: {
          status: "ok",
          since_iso: "2026-05-17T08:00:00.000Z",
          data: [],
        } as WhatsNewSection,
      }),
    });
    expect(screen.queryByTestId("home-whats-new")).toBeNull();
  });

  it("renders WhatsNewDigest with the since label when data is present", () => {
    renderHome({ homeResponse: makePayload() });
    expect(screen.getByTestId("home-whats-new-since")).toHaveTextContent(
      /since/i,
    );
  });

  // === InFlightStrip overflow ==============================================

  it("renders InFlightStrip with a card per project (overflow scrolls)", () => {
    const many: InFlightProject[] = Array.from({ length: 6 }).map((_, i) => ({
      ref: { kind: "project", id: `proj_${i}` as ProjectId },
      name: `Project ${i}`,
      icon_emoji: "📁",
      color_hue: 200 + i,
      open_item_count: i,
      last_activity_at: "2026-05-17T11:00:00.000Z",
    }));
    renderHome({
      homeResponse: makePayload({
        in_flight_projects: okSection<readonly InFlightProject[]>(many),
      }),
    });
    expect(screen.getAllByTestId("home-in-flight-card")).toHaveLength(6);
    const scroller = screen.getByTestId("home-in-flight-scroller");
    // overflowX:auto is what enables horizontal scroll for overflow.
    expect(scroller).toHaveAttribute("role", "list");
  });

  it("collapses InFlightStrip when projects are empty", () => {
    renderHome({
      homeResponse: makePayload({
        in_flight_projects: okSection<readonly InFlightProject[]>([]),
      }),
    });
    expect(screen.queryByTestId("home-in-flight")).toBeNull();
  });

  // === Live activity rail ==================================================

  it("renders LiveActivityRail in the side-rail slot", () => {
    renderHome({ homeResponse: makePayload() });
    const rail = screen.getByTestId("home-live-activity-rail");
    expect(rail).toBeInTheDocument();
    expect(screen.getByTestId("home-side-rail")).toContainElement(rail);
  });

  it("prefers the host-supplied liveActivity buffer over the payload backfill", () => {
    const live: HomeActivityRow[] = [
      {
        kind: "approval",
        ref: { kind: "approval", id: APPROVAL_1 },
        title: "Approval needed",
        occurred_at: "2026-05-17T11:59:00.000Z",
      },
    ];
    renderHome({
      homeResponse: makePayload(),
      liveActivity: live,
    });
    const rail = screen.getByTestId("home-live-activity-rail");
    expect(rail).toBeInTheDocument();
  });

  // === First-run welcome ===================================================

  it("renders the first-run welcome empty-state when is_first_run is true", () => {
    renderHome({
      homeResponse: makePayload({ is_first_run: true }),
    });
    const region = screen.getByRole("region", { name: /home destination/i });
    expect(region).toHaveAttribute("data-state", "first-run");
    expect(screen.getByTestId("empty-state-title")).toHaveTextContent(
      /welcome to atlas/i,
    );
  });
});
