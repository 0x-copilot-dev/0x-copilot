import type {
  ConversationId,
  ItemRef,
  RunId,
  SectionResult,
  SkillId,
} from "@enterprise-search/api-types";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@enterprise-search/chat-transport";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TransportProvider } from "../../providers/TransportProvider";

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
import { HomeDestination, type HomeDestinationProps } from "./HomeDestination";

// === Test scaffolding ======================================================

interface StubSseHandle {
  readonly opts: SseSubscribeOptions;
  readonly closed: { value: boolean };
}

interface StubTransport extends Transport {
  readonly _sseHandles: StubSseHandle[];
}

function makeStubTransport(): StubTransport {
  const handles: StubSseHandle[] = [];
  const t: StubTransport = {
    request<TRes>(_req: TypedRequest): Promise<TRes> {
      return new Promise<TRes>(() => undefined);
    },
    subscribeServerSentEvents(opts: SseSubscribeOptions): SseSubscription {
      const closed = { value: false };
      handles.push({ opts, closed });
      return {
        close() {
          closed.value = true;
        },
      };
    },
    getSession(): Session {
      return { bearer: null };
    },
    capabilities(): TransportCapabilities {
      return {
        substrate: "web",
        nativeSecretStorage: false,
        fileSystemAccess: false,
        clipboardWrite: false,
        openExternal: false,
      };
    },
    _sseHandles: handles,
  };
  return t;
}

function renderHome(
  props: HomeDestinationProps = {},
  transport?: StubTransport,
): { transport: StubTransport } {
  const t = transport ?? makeStubTransport();
  render(
    <TransportProvider transport={t}>
      <HomeDestination {...props} />
    </TransportProvider>,
  );
  return { transport: t };
}

// === Fixtures ==============================================================

function ok<T>(data: T): SectionResult<T> {
  return { status: "ok", data };
}
function err<T>(message: string): SectionResult<T> {
  return { status: "error", error: message };
}
function unavailable<T>(message?: string): SectionResult<T> {
  return { status: "unavailable", error: message };
}

const CONV_1 = "conv_1" as ConversationId;
const RUN_1 = "run_1" as RunId;
const SKILL_1 = "skill_1" as SkillId;

const PINNED: PinnedChatSummary = {
  conversation_id: CONV_1,
  title: "Renewal-uplift exploration",
  last_message_at: "2026-05-17T10:00:00.000Z",
  unread_message_count: 0,
};

const RUN: RecentRunSummary = {
  run_id: RUN_1,
  title: "Q3 forecast refresh",
  status: "succeeded",
  started_at: "2026-05-17T09:30:00.000Z",
};

const FAV: FavoriteToolSummary = {
  skill_id: SKILL_1,
  name: "salesforce.opportunity",
  tool_kind: "skill",
};

const TODO: TodoSummary = {
  todo_id: "todo_1",
  text: "Follow up with finance",
  priority: "high",
  is_overdue: false,
  source_kind: "user",
};

const MEETING: MeetingSummary = {
  meeting_id: "evt_1",
  title: "Q3 review",
  start_iso: "2026-05-17T15:00:00.000Z",
  end_iso: "2026-05-17T16:00:00.000Z",
  attendee_count: 4,
  is_organizer: true,
  source_connector: "google_calendar",
};

const STARRED: StarredProjectSummary = {
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

const ACTIVITY: AgentActivityEntry = {
  id: "act_1",
  kind: "drafted_artifact",
  agent_id: "agent_1",
  agent_name: "Brief Author",
  summary: "Atlas drafted a 4-page brief.",
  created_at: "2026-05-17T10:30:00.000Z",
  target: { kind: "run", id: RUN_1 } as ItemRef,
  tone: "positive",
  artifact_kind: "brief",
  artifact_title: "Acme renewal brief",
  word_count: 1200,
};

function makePayload(over: Partial<HomePayload> = {}): HomePayload {
  return {
    greeting: {
      time_of_day: "morning",
      user_first_name: "Sarah",
      tenant_local_date: "Sun, May 17",
      tenant_local_iso: "2026-05-17T08:00:00.000Z",
      agents_working_count: 2,
      needs_you_count: 1,
    },
    agent_activity: ok<ReadonlyArray<AgentActivityEntry>>([ACTIVITY]),
    pinned_chats: ok<ReadonlyArray<PinnedChatSummary>>([PINNED]),
    recent_runs: ok<ReadonlyArray<RecentRunSummary>>([RUN]),
    favorite_tools: ok<ReadonlyArray<FavoriteToolSummary>>([FAV]),
    todays_focus: ok<ReadonlyArray<TodoSummary>>([TODO]),
    upcoming_meetings: ok<ReadonlyArray<MeetingSummary>>([MEETING]),
    starred_projects: ok<ReadonlyArray<StarredProjectSummary>>([STARRED]),
    quick_actions: [QUICK],
    cached_at: "2026-05-17T10:35:00.000Z",
    ...over,
  };
}

// ===========================================================================

describe("HomeDestination", () => {
  it("renders the skeleton state when homeResponse is null", () => {
    renderHome({ homeResponse: null, enableActivityStream: false });
    const region = screen.getByRole("region", { name: /home destination/i });
    expect(region).toHaveAttribute("data-state", "loading");
    expect(
      screen.getAllByTestId("home-skeleton-section").length,
    ).toBeGreaterThan(0);
  });

  it("renders the greeting from the payload", () => {
    renderHome({
      homeResponse: makePayload(),
      enableActivityStream: false,
    });
    const region = screen.getByRole("region", { name: /home destination/i });
    expect(region).toHaveAttribute("data-state", "ready");
    expect(screen.getByTestId("page-header-title")).toHaveTextContent(
      "Good morning, Sarah.",
    );
    expect(screen.getByTestId("page-header-subtitle")).toHaveTextContent(
      /2 agents working/,
    );
  });

  it("falls back to a nameless greeting when first name is empty (Q5)", () => {
    renderHome({
      homeResponse: makePayload({
        greeting: {
          time_of_day: "afternoon",
          user_first_name: "",
          tenant_local_date: "Sun, May 17",
          tenant_local_iso: "2026-05-17T13:00:00.000Z",
          agents_working_count: 0,
          needs_you_count: 0,
        },
      }),
      enableActivityStream: false,
    });
    expect(screen.getByTestId("page-header-title")).toHaveTextContent(
      "Good afternoon.",
    );
  });

  it("mounts all six visible main sections in fixed order", () => {
    renderHome({ homeResponse: makePayload(), enableActivityStream: false });
    const sectionIds = [
      "home-section-agent_activity",
      "home-section-pinned_chats",
      "home-section-recent_runs",
      "home-section-favorite_tools",
      "home-section-todays_focus",
      "home-section-upcoming_meetings",
    ];
    const rendered = sectionIds.map((id) => screen.getByTestId(id));
    expect(rendered).toHaveLength(6);
    for (let i = 1; i < rendered.length; i++) {
      const prev = rendered[i - 1]!;
      const curr = rendered[i]!;
      const after =
        prev.compareDocumentPosition(curr) & Node.DOCUMENT_POSITION_FOLLOWING;
      expect(after).toBeTruthy();
    }
  });

  it("renders an error empty-state for a section in error", () => {
    renderHome({
      homeResponse: makePayload({
        pinned_chats: err<ReadonlyArray<PinnedChatSummary>>(
          "Could not reach chats service.",
        ),
      }),
      enableActivityStream: false,
    });
    const section = screen.getByTestId("home-section-pinned_chats");
    expect(section).toHaveAttribute("data-section-status", "error");
    expect(section).toHaveTextContent(/could not load this section/i);
    expect(section).toHaveTextContent(/could not reach chats service/i);
    expect(section).toHaveTextContent(/retry section/i);
  });

  it("calls onRetrySection with the failing key when retry is clicked", () => {
    const onRetry = vi.fn();
    renderHome({
      homeResponse: makePayload({
        recent_runs: err<ReadonlyArray<RecentRunSummary>>("Upstream timeout."),
      }),
      onRetrySection: onRetry,
      enableActivityStream: false,
    });
    const section = screen.getByTestId("home-section-recent_runs");
    const retryBtn = section.querySelector(
      "[data-testid='empty-state-action']",
    ) as HTMLButtonElement | null;
    expect(retryBtn).not.toBeNull();
    fireEvent.click(retryBtn!);
    expect(onRetry).toHaveBeenCalledWith("recent_runs");
  });

  it("renders an unavailable section as a 'coming soon' empty-state", () => {
    renderHome({
      homeResponse: makePayload({
        todays_focus: unavailable<ReadonlyArray<TodoSummary>>(
          "Todos coming in Phase 3.",
        ),
      }),
      enableActivityStream: false,
    });
    const section = screen.getByTestId("home-section-todays_focus");
    expect(section).toHaveAttribute("data-section-status", "unavailable");
    expect(section).toHaveTextContent(/todos coming soon/i);
  });

  it("renders the connect-a-calendar CTA when upcoming_meetings is null (Q4)", () => {
    renderHome({
      homeResponse: makePayload({ upcoming_meetings: null }),
      enableActivityStream: false,
    });
    const section = screen.getByTestId("home-section-upcoming_meetings");
    expect(section).toHaveAttribute("data-section-status", "connector_missing");
    expect(section).toHaveTextContent(/connect a calendar/i);
  });

  it("renders the OK section body via the placeholder when data is present", () => {
    renderHome({ homeResponse: makePayload(), enableActivityStream: false });
    expect(screen.getByTestId("home-section-pinned-content")).toHaveAttribute(
      "data-section-count",
      "1",
    );
    expect(
      screen.getByTestId("home-section-recent-runs-content"),
    ).toHaveAttribute("data-section-count", "1");
  });

  it("opens the SSE stream once the first payload arrives", () => {
    const { transport } = renderHome({ homeResponse: makePayload() });
    expect(transport._sseHandles).toHaveLength(1);
    expect(transport._sseHandles[0]!.opts.path).toBe("/v1/home/stream");
    expect(transport._sseHandles[0]!.opts.eventName).toBe("home_activity");
  });

  it("does not open the SSE stream while the response is still loading", () => {
    const { transport } = renderHome({ homeResponse: null });
    expect(transport._sseHandles).toHaveLength(0);
  });

  it("prepends an SSE-delivered activity entry to the merged feed", () => {
    const payload = makePayload({
      agent_activity: ok<ReadonlyArray<AgentActivityEntry>>([ACTIVITY]),
    });
    const { transport } = renderHome({ homeResponse: payload });
    const handle = transport._sseHandles[0]!;
    expect(handle).toBeDefined();

    const newEntry: AgentActivityEntry = {
      ...ACTIVITY,
      id: "act_2",
      summary: "Live entry just arrived.",
    };
    act(() => {
      handle.opts.onMessage(JSON.stringify(newEntry));
    });

    const content = screen.getByTestId("home-section-agent-activity-content");
    expect(content).toHaveAttribute("data-section-count", "2");
  });

  it("closes the SSE stream on unmount", () => {
    const transport = makeStubTransport();
    const { unmount } = render(
      <TransportProvider transport={transport}>
        <HomeDestination homeResponse={makePayload()} />
      </TransportProvider>,
    );
    expect(transport._sseHandles).toHaveLength(1);
    const handle = transport._sseHandles[0]!;
    expect(handle.closed.value).toBe(false);
    unmount();
    expect(handle.closed.value).toBe(true);
  });
});
