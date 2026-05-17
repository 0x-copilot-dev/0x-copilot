import type {
  ConversationId,
  ItemRef,
  RunId,
  SectionResult,
  SkillId,
} from "@enterprise-search/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// TODO(merge): rewire to "@enterprise-search/api-types"
import type {
  AgentActivityEntry,
  FavoriteToolSummary,
  HomePayload,
  MeetingSummary,
  PinnedChatSummary,
  QuickAction,
  RecentRunSummary,
  StarredProjectSummary,
  TodoSummary,
} from "./_home-stub";
import { HomePanel } from "./HomePanel";

function ok<T>(data: T): SectionResult<T> {
  return { status: "ok", data };
}
function err<T>(message: string): SectionResult<T> {
  return { status: "error", error: message };
}

const CONV_1 = "conv_1" as ConversationId;
const RUN_1 = "run_1" as RunId;
const SKILL_1 = "skill_1" as SkillId;

const PROJECT: StarredProjectSummary = {
  project_id: "proj_1",
  name: "Atlas Q3",
  icon_emoji: "🪐",
  color_hue: 220,
  active_thread_count: 3,
  last_activity_at: "2026-05-17T08:00:00.000Z",
};

const QUICK: QuickAction = {
  id: "new_chat",
  label: "New chat",
  icon_name: "plus",
  target: { kind: "chat", id: CONV_1 } as ItemRef,
};

function makePayload(over: Partial<HomePayload> = {}): HomePayload {
  return {
    greeting: {
      time_of_day: "morning",
      user_first_name: "Sarah",
      tenant_local_date: "Sun, May 17",
      tenant_local_iso: "2026-05-17T08:00:00.000Z",
      agents_working_count: 0,
      needs_you_count: 0,
    },
    agent_activity: ok<ReadonlyArray<AgentActivityEntry>>([]),
    pinned_chats: ok<ReadonlyArray<PinnedChatSummary>>([]),
    recent_runs: ok<ReadonlyArray<RecentRunSummary>>([
      {
        run_id: RUN_1,
        title: "x",
        status: "succeeded",
        started_at: "2026-05-17T09:30:00.000Z",
      },
    ]),
    favorite_tools: ok<ReadonlyArray<FavoriteToolSummary>>([
      { skill_id: SKILL_1, name: "x", tool_kind: "skill" },
    ]),
    todays_focus: ok<ReadonlyArray<TodoSummary>>([]),
    upcoming_meetings: ok<ReadonlyArray<MeetingSummary>>([]),
    starred_projects: ok<ReadonlyArray<StarredProjectSummary>>([PROJECT]),
    quick_actions: [QUICK],
    cached_at: "2026-05-17T10:35:00.000Z",
    ...over,
  };
}

describe("HomePanel", () => {
  it("renders the loading state when homeResponse is null", () => {
    render(<HomePanel homeResponse={null} />);
    expect(screen.getByTestId("home-panel-loading")).toBeInTheDocument();
    expect(
      screen.getAllByTestId("home-panel-skeleton-row").length,
    ).toBeGreaterThan(0);
  });

  it("renders both sections when ready", () => {
    render(<HomePanel homeResponse={makePayload()} />);
    expect(
      screen.getByTestId("home-panel-section-starred-projects"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("home-panel-section-quick-actions"),
    ).toBeInTheDocument();
  });

  it("forwards starred-projects + quick-actions counts via the placeholder", () => {
    render(<HomePanel homeResponse={makePayload()} />);
    expect(
      screen.getByTestId("home-panel-starred-projects-content"),
    ).toHaveAttribute("data-section-count", "1");
    expect(
      screen.getByTestId("home-panel-quick-actions-content"),
    ).toHaveAttribute("data-section-count", "1");
  });

  it("renders the starred-projects retry CTA on error and fires the callback", () => {
    const onRetry = vi.fn();
    render(
      <HomePanel
        homeResponse={makePayload({
          starred_projects: err<ReadonlyArray<StarredProjectSummary>>(
            "Could not reach projects service.",
          ),
        })}
        onRetryStarredProjects={onRetry}
      />,
    );
    const section = screen.getByTestId("home-panel-section-starred-projects");
    expect(section).toHaveTextContent(/couldn't load starred projects/i);
    const btn = section.querySelector(
      "[data-testid='empty-state-action']",
    ) as HTMLButtonElement | null;
    expect(btn).not.toBeNull();
    fireEvent.click(btn!);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("renders the empty starred-projects state when the list is empty", () => {
    render(
      <HomePanel
        homeResponse={makePayload({
          starred_projects: ok<ReadonlyArray<StarredProjectSummary>>([]),
        })}
      />,
    );
    expect(
      screen.getByTestId("home-panel-section-starred-projects"),
    ).toHaveTextContent(/no starred projects yet/i);
  });

  it("renders the panel inside an accessible aside region", () => {
    render(<HomePanel homeResponse={makePayload()} />);
    const aside = screen.getByRole("complementary", { name: /home panel/i });
    expect(aside).toBeInTheDocument();
    expect(aside).toHaveAttribute("data-destination", "home");
  });
});
