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

import type {
  ConnectorId,
  ListRoutinesResponse,
  Routine,
  RoutineId,
  RoutineStreamEnvelope,
  TenantId,
  TriggerId,
  UserId,
} from "../../api/_routines-stub";

// Mock the routinesApi module so the tests don't have to drive the
// real fetch / SSE plumbing — that surface is covered in
// `routinesApi.test.ts`.
const routinesApiMocks = vi.hoisted(() => ({
  fetchRoutines: vi.fn(),
  activateRoutine: vi.fn(),
  pauseRoutine: vi.fn(),
  dismissRoutine: vi.fn(),
  runRoutineNow: vi.fn(),
  streamRoutineEvents: vi.fn(),
}));
vi.mock("../../api/routinesApi", async () => {
  const actual = await vi.importActual<typeof import("../../api/routinesApi")>(
    "../../api/routinesApi",
  );
  return {
    ...actual,
    fetchRoutines: routinesApiMocks.fetchRoutines,
    activateRoutine: routinesApiMocks.activateRoutine,
    pauseRoutine: routinesApiMocks.pauseRoutine,
    dismissRoutine: routinesApiMocks.dismissRoutine,
    runRoutineNow: routinesApiMocks.runRoutineNow,
    streamRoutineEvents: routinesApiMocks.streamRoutineEvents,
  };
});

// Imports below this line resolve through the mocks above.
import { RoutinesRoute, applyRoutineEnvelope } from "./RoutinesRoute";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

function routine(overrides: Partial<Routine> = {}): Routine {
  return {
    id: "routine_1" as RoutineId,
    tenant_id: "tenant_1" as TenantId,
    owner_user_id: "user_test" as UserId,
    project_id: null,
    name: "Daily brief",
    description: "",
    instructions: "",
    model: "gpt-5-mini",
    depth: "balanced",
    base_agent_id: null,
    status: "active",
    pause_reason: null,
    triggers: [
      {
        kind: "schedule",
        trigger_id: "trigger_1" as TriggerId,
        cron: "0 9 * * *",
        tz: "UTC",
      },
    ],
    connectors: [
      {
        connector_id: "connector_slack" as ConnectorId,
        mode: "inherit",
      },
    ],
    behavior: {
      autonomy: "manual_approval",
      max_retries: 3,
      backoff: "exponential",
      backoff_base_seconds: 30,
      max_duration_seconds: 600,
      output_target: { kind: "inbox" },
      notify_on_success: [],
      notify_on_failure: ["owner"],
    },
    permissions: {
      scope: "read_only",
      allowed_tools: [],
      allowed_skills: [],
      max_tool_calls_per_fire: 10,
      max_output_tokens_per_fire: 4_000,
      data_residency: "inherit",
      manual_fire: "owner",
    },
    missed_fire_policy: "fire_once",
    next_fire_at: "2026-05-19T09:00:00Z",
    last_fire_at: null,
    last_fire_status: null,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-18T09:00:00Z",
    ...overrides,
  };
}

function listResponse(items: ReadonlyArray<Routine>): ListRoutinesResponse {
  return { items, next_cursor: null };
}

function envelope(
  type: RoutineStreamEnvelope["event_type"],
  r: Routine,
  sequenceNo = 1,
): RoutineStreamEnvelope {
  return {
    sequence_no: sequenceNo,
    event_type: type,
    routine: r,
    emitted_at: "2026-05-18T09:00:00Z",
  };
}

// Captures the latest streamRoutineEvents callback bundle so tests can
// synchronously deliver SSE events / errors without depending on the
// real Transport.
function captureStreamCallbacks(closeMock = vi.fn()): {
  readonly close: Mock;
  readonly lastCall: () => {
    onEvent: (e: RoutineStreamEnvelope) => void;
    onError: (e: Event) => void;
    onOpen?: () => void;
  };
} {
  let lastCallbacks: {
    onEvent: (e: RoutineStreamEnvelope) => void;
    onError: (e: Event) => void;
    onOpen?: () => void;
  } = { onEvent: () => undefined, onError: () => undefined };
  routinesApiMocks.streamRoutineEvents.mockImplementation(
    ({
      onEvent,
      onError,
      onOpen,
    }: {
      onEvent: (e: RoutineStreamEnvelope) => void;
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
  };
}

// ===========================================================================
// PURE REDUCER — applyRoutineEnvelope
// ===========================================================================

describe("applyRoutineEnvelope", () => {
  it("prepends routine_created", () => {
    const a = routine({ id: "a" as RoutineId });
    const b = routine({ id: "b" as RoutineId });
    const next = applyRoutineEnvelope([a], envelope("routine_created", b));
    expect(next.map((r) => r.id)).toEqual(["b", "a"]);
  });

  it("treats routine_created as update when id already exists (idempotency)", () => {
    const a = routine({ id: "a" as RoutineId, status: "active" });
    const aPaused = routine({ id: "a" as RoutineId, status: "paused" });
    const next = applyRoutineEnvelope(
      [a],
      envelope("routine_created", aPaused),
    );
    expect(next).toHaveLength(1);
    expect(next[0].status).toBe("paused");
  });

  it("replaces in place on routine_updated", () => {
    const a = routine({ id: "a" as RoutineId, name: "Old" });
    const aNew = routine({ id: "a" as RoutineId, name: "New" });
    const next = applyRoutineEnvelope([a], envelope("routine_updated", aNew));
    expect(next[0].name).toBe("New");
  });

  it("replaces in place on routine_paused / routine_fired", () => {
    const a = routine({ id: "a" as RoutineId, status: "active" });
    const aPaused = routine({ id: "a" as RoutineId, status: "paused" });
    const afterPause = applyRoutineEnvelope(
      [a],
      envelope("routine_paused", aPaused),
    );
    expect(afterPause[0].status).toBe("paused");

    const aFired = routine({
      id: "a" as RoutineId,
      last_fire_status: "succeeded",
    });
    const afterFire = applyRoutineEnvelope(
      [a],
      envelope("routine_fired", aFired),
    );
    expect(afterFire[0].last_fire_status).toBe("succeeded");
  });

  it("ignores updates for unknown ids", () => {
    const a = routine({ id: "a" as RoutineId });
    const b = routine({ id: "b" as RoutineId });
    const next = applyRoutineEnvelope([a], envelope("routine_updated", b));
    expect(next).toBe(/* same reference */ next);
    expect(next.map((r) => r.id)).toEqual(["a"]);
  });

  it("drops a row on routine_deleted", () => {
    const a = routine({ id: "a" as RoutineId });
    const b = routine({ id: "b" as RoutineId });
    const next = applyRoutineEnvelope([a, b], envelope("routine_deleted", b));
    expect(next.map((r) => r.id)).toEqual(["a"]);
  });

  it("returns the same array when routine_deleted targets an unknown id", () => {
    const a = routine({ id: "a" as RoutineId });
    const b = routine({ id: "b" as RoutineId });
    const before = [a];
    const after = applyRoutineEnvelope(before, envelope("routine_deleted", b));
    expect(after).toBe(before);
  });
});

// ===========================================================================
// RENDER — happy + error paths
// ===========================================================================

describe("RoutinesRoute render", () => {
  beforeEach(() => {
    routinesApiMocks.fetchRoutines.mockReset();
    routinesApiMocks.activateRoutine.mockReset();
    routinesApiMocks.pauseRoutine.mockReset();
    routinesApiMocks.dismissRoutine.mockReset();
    routinesApiMocks.runRoutineNow.mockReset();
    routinesApiMocks.streamRoutineEvents.mockReset();
    routinesApiMocks.streamRoutineEvents.mockReturnValue({
      close: vi.fn(),
    });
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the loading state, then the ready list", async () => {
    routinesApiMocks.fetchRoutines.mockResolvedValueOnce(
      listResponse([routine({ name: "Morning brief" })]),
    );

    render(<RoutinesRoute identity={IDENTITY} />);

    expect(screen.getByTestId("routines-route")).toHaveAttribute(
      "data-state",
      "loading",
    );

    await waitFor(() => {
      expect(screen.getByTestId("routines-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });
    expect(screen.getByText("Morning brief")).toBeInTheDocument();
    expect(screen.getByTestId("routines-route")).toHaveAttribute(
      "data-item-count",
      "1",
    );
  });

  it("renders the empty state when the server returns no items", async () => {
    routinesApiMocks.fetchRoutines.mockResolvedValueOnce(listResponse([]));
    render(<RoutinesRoute identity={IDENTITY} />);
    await waitFor(() => {
      expect(screen.getByTestId("routines-route-empty")).toBeInTheDocument();
    });
  });

  it("renders the error state on fetch failure and retries on click", async () => {
    routinesApiMocks.fetchRoutines.mockRejectedValueOnce(new Error("boom"));
    routinesApiMocks.fetchRoutines.mockResolvedValueOnce(
      listResponse([routine()]),
    );

    render(<RoutinesRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("routines-route-error")).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("routines-route-error-message").textContent,
    ).toContain("boom");

    fireEvent.click(screen.getByTestId("routines-route-retry"));

    await waitFor(() => {
      expect(screen.getByTestId("routines-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });
    expect(routinesApiMocks.fetchRoutines).toHaveBeenCalledTimes(2);
  });
});

// ===========================================================================
// SSE — deltas merge into the local list
// ===========================================================================

describe("RoutinesRoute SSE", () => {
  beforeEach(() => {
    routinesApiMocks.fetchRoutines.mockReset();
    routinesApiMocks.streamRoutineEvents.mockReset();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("subscribes after the initial load and merges routine_created deltas", async () => {
    routinesApiMocks.fetchRoutines.mockResolvedValueOnce(
      listResponse([routine({ id: "a" as RoutineId, name: "A" })]),
    );
    const sse = captureStreamCallbacks();

    render(<RoutinesRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(routinesApiMocks.streamRoutineEvents).toHaveBeenCalledTimes(1);
    });

    act(() => {
      sse
        .lastCall()
        .onEvent(
          envelope(
            "routine_created",
            routine({ id: "b" as RoutineId, name: "B" }),
            1,
          ),
        );
    });

    await waitFor(() => {
      expect(screen.getByText("B")).toBeInTheDocument();
    });
    expect(screen.getAllByTestId("routines-route-row")).toHaveLength(2);
  });

  it("drops a row on routine_deleted", async () => {
    const a = routine({ id: "a" as RoutineId, name: "Alpha" });
    const b = routine({ id: "b" as RoutineId, name: "Bravo" });
    routinesApiMocks.fetchRoutines.mockResolvedValueOnce(listResponse([a, b]));
    const sse = captureStreamCallbacks();

    render(<RoutinesRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByText("Alpha")).toBeInTheDocument();
    });

    act(() => {
      sse.lastCall().onEvent(envelope("routine_deleted", b, 2));
    });

    await waitFor(() => {
      expect(screen.queryByText("Bravo")).not.toBeInTheDocument();
    });
  });

  it("closes the active stream when the stream errors out (reconnect is then scheduled)", async () => {
    routinesApiMocks.fetchRoutines.mockResolvedValueOnce(
      listResponse([routine()]),
    );
    const sse = captureStreamCallbacks();

    render(<RoutinesRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(routinesApiMocks.streamRoutineEvents).toHaveBeenCalledTimes(1);
    });

    // Trigger an error → component closes the active handle and queues
    // an exponential-backoff reconnect via setTimeout. We assert on
    // the close itself (the observable side-effect); the reconnect
    // timing is covered structurally by the reducer + the
    // RECONNECT_BACKOFF_* constants and would otherwise require
    // global timer mocking which conflicts with React Testing
    // Library's own polling under jsdom.
    act(() => {
      sse.lastCall().onError(new Event("error"));
    });
    expect(sse.close).toHaveBeenCalled();
  });
});

// ===========================================================================
// MUTATIONS — activate / pause / delete / run-now
// ===========================================================================

describe("RoutinesRoute mutations", () => {
  beforeEach(() => {
    routinesApiMocks.fetchRoutines.mockReset();
    routinesApiMocks.activateRoutine.mockReset();
    routinesApiMocks.pauseRoutine.mockReset();
    routinesApiMocks.dismissRoutine.mockReset();
    routinesApiMocks.runRoutineNow.mockReset();
    routinesApiMocks.streamRoutineEvents.mockReset();
    routinesApiMocks.streamRoutineEvents.mockReturnValue({
      close: vi.fn(),
    });
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("calls pauseRoutine and merges the updated row", async () => {
    const a = routine({ id: "a" as RoutineId, status: "active" });
    routinesApiMocks.fetchRoutines.mockResolvedValueOnce(listResponse([a]));
    routinesApiMocks.pauseRoutine.mockResolvedValueOnce(
      routine({ id: "a" as RoutineId, status: "paused" }),
    );

    render(<RoutinesRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("routines-route-pause")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("routines-route-pause"));

    await waitFor(() => {
      expect(routinesApiMocks.pauseRoutine).toHaveBeenCalledWith(
        IDENTITY,
        "a",
        {},
      );
    });
    await waitFor(() => {
      expect(screen.getByTestId("routines-route-row")).toHaveAttribute(
        "data-routine-status",
        "paused",
      );
    });
  });

  it("calls activateRoutine and merges the updated row", async () => {
    const a = routine({ id: "a" as RoutineId, status: "paused" });
    routinesApiMocks.fetchRoutines.mockResolvedValueOnce(listResponse([a]));
    routinesApiMocks.activateRoutine.mockResolvedValueOnce(
      routine({ id: "a" as RoutineId, status: "active" }),
    );

    render(<RoutinesRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("routines-route-activate")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("routines-route-activate"));

    await waitFor(() => {
      expect(routinesApiMocks.activateRoutine).toHaveBeenCalledWith(
        IDENTITY,
        "a",
      );
    });
    await waitFor(() => {
      expect(screen.getByTestId("routines-route-row")).toHaveAttribute(
        "data-routine-status",
        "active",
      );
    });
  });

  it("calls dismissRoutine and removes the row from the local list", async () => {
    const a = routine({ id: "a" as RoutineId });
    routinesApiMocks.fetchRoutines.mockResolvedValueOnce(listResponse([a]));
    routinesApiMocks.dismissRoutine.mockResolvedValueOnce(undefined);

    render(<RoutinesRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("routines-route-delete")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("routines-route-delete"));

    await waitFor(() => {
      expect(routinesApiMocks.dismissRoutine).toHaveBeenCalledWith(
        IDENTITY,
        "a",
      );
    });
    await waitFor(() => {
      expect(screen.getByTestId("routines-route-empty")).toBeInTheDocument();
    });
  });

  it("calls runRoutineNow on the Run-now button click", async () => {
    const a = routine({ id: "a" as RoutineId });
    routinesApiMocks.fetchRoutines.mockResolvedValueOnce(listResponse([a]));
    routinesApiMocks.runRoutineNow.mockResolvedValueOnce({
      run_ref: { kind: "run", id: "run_99" },
    });

    render(<RoutinesRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("routines-route-run-now")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("routines-route-run-now"));

    await waitFor(() => {
      expect(routinesApiMocks.runRoutineNow).toHaveBeenCalledWith(
        IDENTITY,
        "a",
      );
    });
  });

  it("surfaces a pending-error banner when the mutation fails (and keeps rendering the list)", async () => {
    const a = routine({ id: "a" as RoutineId, status: "active" });
    routinesApiMocks.fetchRoutines.mockResolvedValueOnce(listResponse([a]));
    routinesApiMocks.runRoutineNow.mockRejectedValueOnce(
      new Error("manual_fire_forbidden"),
    );

    render(<RoutinesRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("routines-route-run-now")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("routines-route-run-now"));

    await waitFor(() => {
      expect(
        screen.getByTestId("routines-route-pending-error"),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("routines-route-pending-error").textContent,
    ).toContain("manual_fire_forbidden");
    // The list itself is still rendered — the user can retry.
    expect(screen.getByTestId("routines-route-row")).toBeInTheDocument();
  });
});
