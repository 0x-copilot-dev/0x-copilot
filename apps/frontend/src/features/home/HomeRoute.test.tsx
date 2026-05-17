import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type Mock,
} from "vitest";

import type { HomeResponse } from "../../api/_home-stub";

// Mock chat-surface BEFORE HomeRoute pulls it in:
//   - <HomeDestination /> would otherwise need full TransportProvider +
//     RouterProvider scaffolding to mount. The route under test owns
//     the data flow; the destination's render is exercised in its own
//     test suite.
//   - useKeyValueStore returns an in-memory mock store so we can drive
//     the activity-window setting without rebuilding LocalStorageKVS.
const kvStore = createMockKvStore();
vi.mock("@enterprise-search/chat-surface", () => ({
  HomeDestination: () => <div data-testid="home-destination-stub">stub</div>,
  useKeyValueStore: () => kvStore,
}));

// Mock the homeApi module so the tests don't have to drive the real
// fetch / SSE plumbing — that surface is covered in homeApi.test.ts.
// We export factory functions that return controllable handles so each
// test can pick its own resolution order.
const homeApiMocks = vi.hoisted(() => ({
  fetchHome: vi.fn(),
  streamHomeActivity: vi.fn(),
}));
vi.mock("../../api/homeApi", async () => {
  const actual =
    await vi.importActual<typeof import("../../api/homeApi")>(
      "../../api/homeApi",
    );
  return {
    ...actual,
    fetchHome: homeApiMocks.fetchHome,
    streamHomeActivity: homeApiMocks.streamHomeActivity,
  };
});

// Imports below this line resolve through the mocks above.
import { HomeRoute, HOME_ACTIVITY_WINDOW_HOURS_KEY } from "./HomeRoute";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

function createMockKvStore(): {
  get: (k: string) => string | null;
  set: (k: string, v: string | null) => void;
  keys: (prefix?: string) => readonly string[];
  __reset: () => void;
  __seed: (k: string, v: string) => void;
} {
  const data = new Map<string, string>();
  return {
    get: (k) => data.get(k) ?? null,
    set: (k, v) => {
      if (v === null) data.delete(k);
      else data.set(k, v);
    },
    keys: (prefix) => {
      const all = Array.from(data.keys());
      return prefix ? all.filter((k) => k.startsWith(prefix)) : all;
    },
    __reset: () => data.clear(),
    __seed: (k, v) => data.set(k, v),
  };
}

function homeResponse(): HomeResponse {
  return {
    greeting: {
      time_of_day: "morning",
      user_first_name: "Sarah",
      tenant_local_date: "2026-05-18",
      tenant_local_iso: "2026-05-18T09:00:00Z",
      agents_working_count: 0,
      needs_you_count: 0,
    },
    agent_activity: { status: "ok", data: [] },
    pinned_chats: { status: "ok", data: [] },
    recent_runs: { status: "ok", data: [] },
    favorite_tools: { status: "ok", data: [] },
    todays_focus: { status: "ok", data: [] },
    upcoming_meetings: null,
    starred_projects: { status: "ok", data: [] },
    quick_actions: [],
    cached_at: "2026-05-18T09:00:00Z",
  };
}

// Captures the latest streamHomeActivity callback bundle so a test
// can synchronously deliver SSE events / errors without depending on
// the real Transport.
function captureStreamCallbacks(closeMock = vi.fn()): {
  readonly close: Mock;
  readonly lastCall: () => {
    onEvent: (e: unknown) => void;
    onError: (e: Event) => void;
    onOpen?: () => void;
  };
} {
  homeApiMocks.streamHomeActivity.mockImplementation(
    ({
      onEvent,
      onError,
      onOpen,
    }: {
      onEvent: (e: unknown) => void;
      onError: (e: Event) => void;
      onOpen?: () => void;
    }) => {
      lastCallbacks = { onEvent, onError, onOpen };
      return { close: closeMock };
    },
  );
  let lastCallbacks: {
    onEvent: (e: unknown) => void;
    onError: (e: Event) => void;
    onOpen?: () => void;
  } = { onEvent: () => undefined, onError: () => undefined };
  return {
    close: closeMock,
    lastCall: () => lastCallbacks,
  };
}

describe("HomeRoute", () => {
  beforeEach(() => {
    kvStore.__reset();
    homeApiMocks.fetchHome.mockReset();
    homeApiMocks.streamHomeActivity.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the destination after the home payload loads", async () => {
    homeApiMocks.fetchHome.mockResolvedValue(homeResponse());
    captureStreamCallbacks();

    render(<HomeRoute identity={IDENTITY} />);

    // Initially the route is in `loading` — destination renders its
    // own skeleton inside, route just wraps.
    expect(screen.getByTestId("home-route")).toHaveAttribute(
      "data-state",
      "loading",
    );

    await waitFor(() =>
      expect(screen.getByTestId("home-route")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );
    expect(screen.getByTestId("home-destination-stub")).toBeInTheDocument();
    // Default window is 24 — exposed via data attribute so a
    // downstream test (or future A11y assertion) can introspect.
    expect(screen.getByTestId("home-route")).toHaveAttribute(
      "data-activity-window-hours",
      "24",
    );
    expect(homeApiMocks.fetchHome).toHaveBeenCalledWith(IDENTITY, {
      activityWindowHours: 24,
    });
  });

  it("renders an error state with a working retry when the fetch fails", async () => {
    homeApiMocks.fetchHome.mockRejectedValueOnce(
      new Error("home aggregator unavailable"),
    );
    homeApiMocks.fetchHome.mockResolvedValueOnce(homeResponse());
    captureStreamCallbacks();

    render(<HomeRoute identity={IDENTITY} />);

    await waitFor(() =>
      expect(screen.getByTestId("home-route-error")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("home-route-error-message")).toHaveTextContent(
      /home aggregator unavailable/,
    );
    // Loading-state stream MUST NOT open — gated on `state.kind === "ready"`.
    expect(homeApiMocks.streamHomeActivity).not.toHaveBeenCalled();

    // Retry switches back to loading → ready on the second resolved fetch.
    fireEvent.click(screen.getByTestId("home-route-retry"));
    await waitFor(() =>
      expect(screen.getByTestId("home-route")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );
    expect(homeApiMocks.fetchHome).toHaveBeenCalledTimes(2);
  });

  it("reads the per-user activity-window KV value and forwards it", async () => {
    kvStore.__seed(HOME_ACTIVITY_WINDOW_HOURS_KEY, "168");
    homeApiMocks.fetchHome.mockResolvedValue(homeResponse());
    captureStreamCallbacks();

    render(<HomeRoute identity={IDENTITY} />);

    await waitFor(() =>
      expect(screen.getByTestId("home-route")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );
    expect(homeApiMocks.fetchHome).toHaveBeenCalledWith(IDENTITY, {
      activityWindowHours: 168,
    });
    expect(screen.getByTestId("home-route")).toHaveAttribute(
      "data-activity-window-hours",
      "168",
    );
  });

  it("falls back to the default window when the KV value is not in the allowlist", async () => {
    kvStore.__seed(HOME_ACTIVITY_WINDOW_HOURS_KEY, "9999");
    homeApiMocks.fetchHome.mockResolvedValue(homeResponse());
    captureStreamCallbacks();

    render(<HomeRoute identity={IDENTITY} />);

    await waitFor(() =>
      expect(screen.getByTestId("home-route")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );
    // 9999 is not in {6,12,24,48,168} — defence-in-depth fallback to
    // the default. If a corrupted KV value leaked through, the backend
    // would 400 and the user would see an unrecoverable error state.
    expect(homeApiMocks.fetchHome).toHaveBeenCalledWith(IDENTITY, {
      activityWindowHours: 24,
    });
  });

  it("merges an SSE activity event into the loaded payload", async () => {
    homeApiMocks.fetchHome.mockResolvedValue(homeResponse());
    const capture = captureStreamCallbacks();

    render(<HomeRoute identity={IDENTITY} />);

    await waitFor(() =>
      expect(screen.getByTestId("home-route")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );
    // The SSE handle must have been opened — sub-PRD §3.5 requires
    // the live feed exactly while Home is mounted.
    expect(homeApiMocks.streamHomeActivity).toHaveBeenCalledTimes(1);
    const sseArgs = homeApiMocks.streamHomeActivity.mock.calls[0][0];
    expect(sseArgs.identity).toEqual(IDENTITY);
    expect(sseArgs.activityWindowHours).toBe(24);

    // Deliver a synthetic activity event. The route stays ready (no
    // remount) and the data-attribute remains stable — this exercises
    // the "SSE update does not crash the page" invariant.
    act(() => {
      capture.lastCall().onOpen?.();
      capture.lastCall().onEvent({
        id: "act_1",
        kind: "drafted_artifact",
        agent_id: "agent_1",
        agent_name: "Atlas",
        summary: "Atlas drafted a 4-page brief.",
        created_at: "2026-05-18T09:00:00Z",
        target: { kind: "run", id: "run_1" },
        tone: "neutral",
      });
    });
    expect(screen.getByTestId("home-route")).toHaveAttribute(
      "data-state",
      "ready",
    );
  });

  it("reconnects with exponential backoff after an SSE error", async () => {
    vi.useFakeTimers();
    homeApiMocks.fetchHome.mockResolvedValue(homeResponse());
    captureStreamCallbacks();

    render(<HomeRoute identity={IDENTITY} />);

    // Resolve the initial fetch.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(homeApiMocks.streamHomeActivity).toHaveBeenCalledTimes(1);
    const firstCallbacks = homeApiMocks.streamHomeActivity.mock.calls[0][0] as {
      onError: (e: Event) => void;
    };

    // Trigger an SSE failure — should schedule a 1s retry (min backoff).
    act(() => {
      firstCallbacks.onError(new Event("error"));
    });
    expect(homeApiMocks.streamHomeActivity).toHaveBeenCalledTimes(1);
    act(() => {
      vi.advanceTimersByTime(1_000);
    });
    expect(homeApiMocks.streamHomeActivity).toHaveBeenCalledTimes(2);

    // Second failure doubles the backoff to 2s.
    const secondCallbacks = homeApiMocks.streamHomeActivity.mock
      .calls[1][0] as {
      onError: (e: Event) => void;
    };
    act(() => {
      secondCallbacks.onError(new Event("error"));
    });
    // Before the timeout fires, still only 2 attempts.
    act(() => {
      vi.advanceTimersByTime(1_500);
    });
    expect(homeApiMocks.streamHomeActivity).toHaveBeenCalledTimes(2);
    act(() => {
      vi.advanceTimersByTime(500);
    });
    expect(homeApiMocks.streamHomeActivity).toHaveBeenCalledTimes(3);
  });

  it("closes the SSE handle on unmount", async () => {
    homeApiMocks.fetchHome.mockResolvedValue(homeResponse());
    const close = vi.fn();
    captureStreamCallbacks(close);

    const view = render(<HomeRoute identity={IDENTITY} />);

    await waitFor(() =>
      expect(homeApiMocks.streamHomeActivity).toHaveBeenCalledTimes(1),
    );

    view.unmount();

    expect(close).toHaveBeenCalled();
  });
});
