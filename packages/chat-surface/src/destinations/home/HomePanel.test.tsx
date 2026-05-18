// HomePanel — Phase 9 panel tests.
//
// Phase 9 drops StarredProjects from the panel; QuickActions is the
// only section.

import type {
  HomeActivityRow,
  HomePayload,
  InFlightProject,
  QuickAction,
  QuickActionTarget,
  SectionResult,
  TimelineEntry,
  WhatsNewSection,
} from "@enterprise-search/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { HomePanel } from "./HomePanel";

function okSection<T>(data: T): SectionResult<T> {
  return { status: "ok", data };
}

const NEW_CHAT: QuickAction = {
  id: "new_chat",
  label: "New chat",
  icon_name: "plus",
  target: { kind: "chat_new" },
};

const NEW_TODO: QuickAction = {
  id: "new_todo",
  label: "New todo",
  icon_name: "check",
  target: { kind: "todo_new" },
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
      approvals_waiting: 0,
      runs_failed_24h: 0,
      todos_overdue: 0,
      todos_due_today: 0,
    },
    today_timeline: okSection<readonly TimelineEntry[]>([]),
    whats_new: {
      status: "ok",
      since_iso: "2026-05-17T08:00:00.000Z",
      data: [],
    } as WhatsNewSection,
    in_flight_projects: okSection<readonly InFlightProject[]>([]),
    live_activity: okSection<readonly HomeActivityRow[]>([]),
    quick_actions: [NEW_CHAT, NEW_TODO],
    cached_at: "2026-05-17T10:35:00.000Z",
    is_first_run: false,
    ...over,
  };
}

describe("HomePanel — Phase 9", () => {
  it("renders the loading state when homeResponse is null", () => {
    render(<HomePanel homeResponse={null} />);
    expect(screen.getByTestId("home-panel-loading")).toBeInTheDocument();
    expect(
      screen.getAllByTestId("home-panel-skeleton-row").length,
    ).toBeGreaterThan(0);
  });

  it("renders only the quick-actions section when ready", () => {
    render(<HomePanel homeResponse={makePayload()} />);
    expect(
      screen.getByTestId("home-panel-section-quick-actions"),
    ).toBeInTheDocument();
    // Starred-projects section is retired in Phase 9.
    expect(
      screen.queryByTestId("home-panel-section-starred-projects"),
    ).toBeNull();
  });

  it("renders one tile per quick action", () => {
    render(<HomePanel homeResponse={makePayload()} />);
    expect(screen.getAllByTestId("home-quick-action")).toHaveLength(2);
  });

  it("fires onQuickActionSelect with the typed target on click", () => {
    const onQuickActionSelect = vi.fn<(t: QuickActionTarget) => void>();
    render(
      <HomePanel
        homeResponse={makePayload()}
        onQuickActionSelect={onQuickActionSelect}
      />,
    );
    fireEvent.click(screen.getAllByTestId("home-quick-action")[0]!);
    expect(onQuickActionSelect).toHaveBeenCalledTimes(1);
    expect(onQuickActionSelect.mock.calls[0]?.[0]).toEqual({
      kind: "chat_new",
    });
  });

  it("renders the empty quick-actions state when the list is empty", () => {
    render(<HomePanel homeResponse={makePayload({ quick_actions: [] })} />);
    expect(
      screen.getByTestId("home-panel-section-quick-actions"),
    ).toHaveTextContent(/no quick actions yet/i);
  });

  it("renders the panel inside an accessible aside region", () => {
    render(<HomePanel homeResponse={makePayload()} />);
    const aside = screen.getByRole("complementary", { name: /home panel/i });
    expect(aside).toBeInTheDocument();
    expect(aside).toHaveAttribute("data-destination", "home");
  });
});
