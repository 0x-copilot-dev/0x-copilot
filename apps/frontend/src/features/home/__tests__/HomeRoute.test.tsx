import { act, render, screen, waitFor } from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type Mock,
} from "vitest";

import type { HomePayload } from "@enterprise-search/api-types";

// Mock chat-surface BEFORE HomeRoute pulls it in. Capture the props each
// shell receives so we can introspect the v2 hand-off in tests without
// rebuilding the TransportProvider scaffolding the real components need.
const homeDestinationProps: { current: Record<string, unknown> | null } = {
  current: null,
};
const homePanelProps: { current: Record<string, unknown> | null } = {
  current: null,
};
vi.mock("@enterprise-search/chat-surface", () => ({
  HomeDestination: (props: Record<string, unknown>) => {
    homeDestinationProps.current = props;
    return <div data-testid="home-destination-stub">destination</div>;
  },
  HomePanel: (props: Record<string, unknown>) => {
    homePanelProps.current = props;
    return <div data-testid="home-panel-stub">panel</div>;
  },
}));

// Mock homeApi so the tests don't have to drive real fetch / SSE
// plumbing — those surfaces are covered in homeApi.test.ts.
const homeApiMocks = vi.hoisted(() => ({
  fetchHome: vi.fn(),
  openHomeStream: vi.fn(),
}));
vi.mock("../../../api/homeApi", async () => {
  const actual = await vi.importActual<typeof import("../../../api/homeApi")>(
    "../../../api/homeApi",
  );
  return {
    ...actual,
    fetchHome: homeApiMocks.fetchHome,
    openHomeStream: homeApiMocks.openHomeStream,
  };
});

// Imports below this line resolve through the mocks above.
import { HomeRoute } from "../HomeRoute";
import type { HomeStreamEnvelope } from "../../../api/homeApi";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

/**
 * Minimal Phase 9 `HomePayload` — every required field at the wire
 * defaults so contract drift surfaces as a TS failure rather than a
 * runtime surprise.
 */
function homePayload(): HomePayload {
  return {
    greeting: {
      display_name: "Sarah",
      time_segment: "morning",
      tenant_local_date: "2026-05-18",
      tenant_local_iso: "2026-05-18T09:00:00Z",
    },
    triage: {
      approvals_waiting: 1,
      runs_failed_24h: 0,
      todos_overdue: 0,
      todos_due_today: 2,
    },
    today_timeline: { status: "ok", data: [] },
    whats_new: {
      status: "ok",
      since_iso: "2026-05-18T07:42:00Z",
      data: [],
    },
    in_flight_projects: { status: "ok", data: [] },
    live_activity: { status: "ok", data: [] },
    quick_actions: [],
    cached_at: "2026-05-18T09:00:00Z",
    is_first_run: false,
  };
}

// Capture the latest openHomeStream callbacks so a test can synchronously
// deliver envelope events / errors without touching the real Transport.
function captureStreamCallbacks(closeMock = vi.fn()): {
  readonly close: Mock;
  readonly lastCall: () => {
    onEvent: (e: HomeStreamEnvelope) => void;
    onError: (e: Event) => void;
    onOpen?: () => void;
  };
  readonly callCount: () => number;
} {
  let lastCallbacks: {
    onEvent: (e: HomeStreamEnvelope) => void;
    onError: (e: Event) => void;
    onOpen?: () => void;
  } = {
    onEvent: () => undefined,
    onError: () => undefined,
  };
  homeApiMocks.openHomeStream.mockImplementation(
    ({
      onEvent,
      onError,
      onOpen,
    }: {
      onEvent: (e: HomeStreamEnvelope) => void;
      onError: (e: Event) => void;
      onOpen?: () => void;
    }) => {
      lastCallbacks = { onEvent, onError, onOpen };
      return { close: closeMock };
    },
  );
  return {
    close: closeMock,
    lastCall: () => lastCallbacks,
    callCount: () => homeApiMocks.openHomeStream.mock.calls.length,
  };
}

describe("HomeRoute (Phase 9 v2)", () => {
  beforeEach(() => {
    homeApiMocks.fetchHome.mockReset();
    homeApiMocks.openHomeStream.mockReset();
    homeDestinationProps.current = null;
    homePanelProps.current = null;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the v2 destination + panel after the home payload loads", async () => {
    const payload = homePayload();
    homeApiMocks.fetchHome.mockResolvedValue(payload);
    captureStreamCallbacks();

    render(<HomeRoute identity={IDENTITY} />);

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
    expect(screen.getByTestId("home-panel-stub")).toBeInTheDocument();

    // V2 prop hand-off: route ships the full `HomePayload` to both
    // shells via the chat-surface P9-B prop name `homeResponse`.
    expect(homeDestinationProps.current?.homeResponse).toBe(payload);
    expect(homePanelProps.current?.homeResponse).toBe(payload);

    expect(homeApiMocks.fetchHome).toHaveBeenCalledWith(IDENTITY);
  });

  it("merges a home.triage_updated event into the cached payload", async () => {
    const initial = homePayload();
    homeApiMocks.fetchHome.mockResolvedValue(initial);
    const capture = captureStreamCallbacks();

    render(<HomeRoute identity={IDENTITY} />);

    await waitFor(() =>
      expect(screen.getByTestId("home-route")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );

    expect(capture.callCount()).toBe(1);
    expect(
      (homeDestinationProps.current?.homeResponse as HomePayload).triage,
    ).toEqual({
      approvals_waiting: 1,
      runs_failed_24h: 0,
      todos_overdue: 0,
      todos_due_today: 2,
    });

    // Deliver one triage update. The reducer in `./adapters` swaps the
    // triage block; everything else on the payload is preserved.
    act(() => {
      capture.lastCall().onOpen?.();
      capture.lastCall().onEvent({
        type: "home.triage_updated",
        sequence_no: 1,
        triage: {
          approvals_waiting: 3,
          runs_failed_24h: 1,
          todos_overdue: 0,
          todos_due_today: 2,
        },
      });
    });

    const merged = homeDestinationProps.current?.homeResponse as HomePayload;
    expect(merged.triage).toEqual({
      approvals_waiting: 3,
      runs_failed_24h: 1,
      todos_overdue: 0,
      todos_due_today: 2,
    });
    // Other top-level fields are unchanged by reference (the reducer is
    // surgical — only `triage` rebuilt).
    expect(merged.greeting).toBe(initial.greeting);
    expect(merged.quick_actions).toBe(initial.quick_actions);
  });

  it("reconnects with exponential backoff 1s -> 2s -> 4s (cross-audit §1.4)", async () => {
    vi.useFakeTimers();
    homeApiMocks.fetchHome.mockResolvedValue(homePayload());
    captureStreamCallbacks();

    render(<HomeRoute identity={IDENTITY} />);

    // Drain the fetch microtasks so the SSE effect fires.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(homeApiMocks.openHomeStream).toHaveBeenCalledTimes(1);

    // 1st failure -> 1s delay.
    const cb1 = homeApiMocks.openHomeStream.mock.calls[0][0] as {
      onError: (e: Event) => void;
    };
    act(() => {
      cb1.onError(new Event("error"));
    });
    expect(homeApiMocks.openHomeStream).toHaveBeenCalledTimes(1);
    act(() => {
      vi.advanceTimersByTime(999);
    });
    expect(homeApiMocks.openHomeStream).toHaveBeenCalledTimes(1);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(homeApiMocks.openHomeStream).toHaveBeenCalledTimes(2);

    // 2nd failure -> 2s delay (backoff doubled).
    const cb2 = homeApiMocks.openHomeStream.mock.calls[1][0] as {
      onError: (e: Event) => void;
    };
    act(() => {
      cb2.onError(new Event("error"));
    });
    act(() => {
      vi.advanceTimersByTime(1_999);
    });
    expect(homeApiMocks.openHomeStream).toHaveBeenCalledTimes(2);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(homeApiMocks.openHomeStream).toHaveBeenCalledTimes(3);

    // 3rd failure -> 4s delay (backoff doubled again).
    const cb3 = homeApiMocks.openHomeStream.mock.calls[2][0] as {
      onError: (e: Event) => void;
    };
    act(() => {
      cb3.onError(new Event("error"));
    });
    act(() => {
      vi.advanceTimersByTime(3_999);
    });
    expect(homeApiMocks.openHomeStream).toHaveBeenCalledTimes(3);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(homeApiMocks.openHomeStream).toHaveBeenCalledTimes(4);
  });

  it("caps backoff at 30s after enough doublings (1->2->4->8->16->30)", async () => {
    vi.useFakeTimers();
    homeApiMocks.fetchHome.mockResolvedValue(homePayload());
    captureStreamCallbacks();

    render(<HomeRoute identity={IDENTITY} />);

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    const expectedDelays = [1_000, 2_000, 4_000, 8_000, 16_000, 30_000, 30_000];
    for (let i = 0; i < expectedDelays.length; i++) {
      expect(homeApiMocks.openHomeStream).toHaveBeenCalledTimes(i + 1);
      const cb = homeApiMocks.openHomeStream.mock.calls[i][0] as {
        onError: (e: Event) => void;
      };
      act(() => {
        cb.onError(new Event("error"));
      });
      act(() => {
        vi.advanceTimersByTime(expectedDelays[i]);
      });
    }
    expect(homeApiMocks.openHomeStream).toHaveBeenCalledTimes(
      expectedDelays.length + 1,
    );
  });

  it("resumes with the highest applied sequence_no on reconnect", async () => {
    vi.useFakeTimers();
    homeApiMocks.fetchHome.mockResolvedValue(homePayload());
    const capture = captureStreamCallbacks();

    render(<HomeRoute identity={IDENTITY} />);

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(
      (
        homeApiMocks.openHomeStream.mock.calls[0][0] as {
          afterSequence: number;
        }
      ).afterSequence,
    ).toBe(0);

    // Apply two events, then drop the stream.
    act(() => {
      capture.lastCall().onEvent({
        type: "home.heartbeat",
        sequence_no: 7,
      });
      capture.lastCall().onEvent({
        type: "home.heartbeat",
        sequence_no: 12,
      });
      capture.lastCall().onError(new Event("error"));
    });
    act(() => {
      vi.advanceTimersByTime(1_000);
    });

    expect(homeApiMocks.openHomeStream).toHaveBeenCalledTimes(2);
    expect(
      (
        homeApiMocks.openHomeStream.mock.calls[1][0] as {
          afterSequence: number;
        }
      ).afterSequence,
    ).toBe(12);
  });

  it("renders an error state when the fetch fails (401/503 surface here)", async () => {
    homeApiMocks.fetchHome.mockRejectedValueOnce(
      new Error("home aggregator unavailable"),
    );
    captureStreamCallbacks();

    render(<HomeRoute identity={IDENTITY} />);

    await waitFor(() =>
      expect(screen.getByTestId("home-route-error")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("home-route-error-message")).toHaveTextContent(
      /home aggregator unavailable/,
    );
    // SSE must not open on a loading/error route.
    expect(homeApiMocks.openHomeStream).not.toHaveBeenCalled();
  });

  it("closes the SSE handle on unmount", async () => {
    homeApiMocks.fetchHome.mockResolvedValue(homePayload());
    const close = vi.fn();
    captureStreamCallbacks(close);

    const view = render(<HomeRoute identity={IDENTITY} />);

    await waitFor(() =>
      expect(homeApiMocks.openHomeStream).toHaveBeenCalledTimes(1),
    );

    view.unmount();

    expect(close).toHaveBeenCalled();
  });
});
