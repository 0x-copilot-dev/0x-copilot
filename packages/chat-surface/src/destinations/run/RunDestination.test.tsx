// RunDestination — cockpit-shell composition tests (PR-3.5).
//
// The shell is exercised through the same port fakes the pieces use: a
// Transport that resolves the run list + captures SSE subscriptions, and a
// Map-backed KeyValueStore for the Studio/Focus mode. The assertions cover the
// PR-3.5 contract: header (kicker + goal) + ThreadCanvas render from the session,
// the header segmented control toggles + persists the mode, and a stream error
// surfaces a non-blocking Retry banner (FR-3.32) without unmounting the canvas.

import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { type ReactElement } from "react";
import { describe, expect, it } from "vitest";

import type { ConversationId } from "@0x-copilot/api-types";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";

import { KeyValueStoreProvider } from "../../providers/KeyValueStoreProvider";
import { TransportProvider } from "../../providers/TransportProvider";
import type { KeyValueStore } from "../../storage/key-value-store";
import { RunDestination } from "./RunDestination";
import { runModeKey } from "./useRunMode";

const CONV = "conv-1" as ConversationId;

const CAPABILITIES: TransportCapabilities = {
  substrate: "web",
  nativeSecretStorage: false,
  fileSystemAccess: false,
  clipboardWrite: false,
  openExternal: false,
};

interface CapturedSub {
  readonly path: string;
  readonly eventName?: string;
  readonly onError?: (err: Error) => void;
  readonly onMessage?: (raw: string) => void;
  closed: boolean;
}

class FakeTransport implements Transport {
  requestHandler: (req: TypedRequest) => Promise<unknown> = async (req) =>
    req.path.includes("/messages") ? { messages: [] } : { runs: [] };
  readonly requests: TypedRequest[] = [];
  readonly subs: CapturedSub[] = [];

  async request<TRes>(req: TypedRequest): Promise<TRes> {
    this.requests.push(req);
    return (await this.requestHandler(req)) as TRes;
  }

  subscribeServerSentEvents(opts: SseSubscribeOptions): SseSubscription {
    const sub: CapturedSub = {
      path: opts.path,
      eventName: opts.eventName,
      onError: opts.onError,
      onMessage: opts.onMessage,
      closed: false,
    };
    this.subs.push(sub);
    return {
      close: () => {
        sub.closed = true;
      },
    };
  }

  /**
   * Deliver one run event to EVERY open subscriber on its stream path — the
   * session tail (`useRunSession`) AND `TcSwimlanes`' own incremental
   * subscription both listen on `/v1/agent/runs/{id}/stream`, so one emit
   * feeds the single canonical projection (fleet card + Agents count) and the
   * lane stream together (FR-3.17).
   */
  emit(envelope: Record<string, unknown>): void {
    const raw = JSON.stringify(envelope);
    const path = `/v1/agent/runs/${String(envelope.run_id)}/stream`;
    for (const sub of this.subs) {
      if (!sub.closed && sub.path === path) {
        sub.onMessage?.(raw);
      }
    }
  }

  /** The swimlane's un-named subscription on the run stream (lane liveness). */
  get swimlaneSub(): CapturedSub | undefined {
    return this.subs.find(
      (sub) =>
        !sub.closed &&
        sub.eventName === undefined &&
        sub.path.endsWith("/stream"),
    );
  }

  getSession(): Session {
    return { bearer: null };
  }

  capabilities(): TransportCapabilities {
    return CAPABILITIES;
  }

  /**
   * The `useRunSession` subscription specifically — it is the only one tagged
   * with the `runtime_event` name (TcSwimlanes opens its own un-named sub on the
   * same path for lane liveness), so tests target the session's stream cleanly.
   */
  get sessionSub(): CapturedSub | undefined {
    return [...this.subs]
      .reverse()
      .find((sub) => !sub.closed && sub.eventName === "runtime_event");
  }
}

function makeStore(): KeyValueStore {
  const map = new Map<string, string>();
  return {
    get: (key) => map.get(key) ?? null,
    set: (key, value) => {
      if (value === null) {
        map.delete(key);
      } else {
        map.set(key, value);
      }
    },
    keys: (prefix) =>
      [...map.keys()].filter(
        (key) => prefix === undefined || key.startsWith(prefix),
      ),
  };
}

function renderRun(
  transport: Transport,
  store: KeyValueStore,
  conversationId: ConversationId = CONV,
) {
  const ui: ReactElement = (
    <TransportProvider transport={transport}>
      <KeyValueStoreProvider store={store}>
        <RunDestination conversationId={conversationId} />
      </KeyValueStoreProvider>
    </TransportProvider>
  );
  return render(ui);
}

function runningRun(goal: string) {
  return { runs: [{ run_id: "run-1", status: "running", goal }] };
}

describe("RunDestination — shell composition", () => {
  it("renders the run header (kicker + goal) and the thread canvas from the session", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages")
        ? { messages: [] }
        : runningRun("Ship the renewal batch");

    renderRun(transport, makeStore());

    // Header + canvas mount immediately; the goal fills in once the run resolves.
    expect(screen.getByTestId("run-header")).not.toBeNull();
    expect(screen.getByTestId("thread-canvas")).not.toBeNull();
    expect(screen.getByTestId("run-header-kicker").textContent).toBe(
      "ACTIVE RUN",
    );
    await waitFor(() =>
      expect(screen.getByTestId("run-header-goal").textContent).toBe(
        "Ship the renewal batch",
      ),
    );
    // The resolved run binds the session's SSE tail (canonical event source).
    await waitFor(() =>
      expect(transport.sessionSub?.path).toBe("/v1/agent/runs/run-1/stream"),
    );
  });

  it("defaults to Studio and toggles to Focus via the header control, persisting the mode", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages") ? { messages: [] } : runningRun("Goal");
    const store = makeStore();

    renderRun(transport, store);

    const root = screen.getByTestId("run-destination");
    const canvas = screen.getByTestId("thread-canvas");
    expect(root.getAttribute("data-mode")).toBe("studio");
    expect(canvas.getAttribute("data-mode")).toBe("studio");

    fireEvent.click(screen.getByTestId("run-mode-focus"));

    // Both the shell root and the canvas reflect the new mode; no separate
    // canvas remount (the mode is a single controlled value).
    expect(root.getAttribute("data-mode")).toBe("focus");
    expect(canvas.getAttribute("data-mode")).toBe("focus");
    // Persisted to the KeyValueStore under the per-conversation key.
    await waitFor(() => expect(store.get(runModeKey(CONV))).toBe("focus"));
  });

  it("surfaces a non-blocking Retry banner when the run stream errors, keeping the canvas", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages") ? { messages: [] } : runningRun("Goal");
    renderRun(transport, makeStore());

    await waitFor(() => expect(transport.sessionSub).toBeDefined());
    const failedSub = transport.sessionSub;
    act(() => {
      failedSub?.onError?.(new Error("stream dropped"));
    });

    const banner = await screen.findByTestId("run-error-banner");
    expect(banner.getAttribute("role")).toBe("alert");
    // The canvas is NOT replaced by the error — last-projected state stays.
    expect(screen.getByTestId("thread-canvas")).not.toBeNull();

    fireEvent.click(screen.getByTestId("run-error-retry"));
    // Retry re-subscribes from the resume cursor (a fresh, distinct sub).
    await waitFor(() => expect(transport.sessionSub).not.toBe(failedSub));
    expect(failedSub?.closed).toBe(true);
  });

  it("renders without a run (idle) — no error banner, canvas still mounted", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages") ? { messages: [] } : { runs: [] };
    renderRun(transport, makeStore());

    await waitFor(() => {
      const root = screen.getByTestId("run-destination");
      expect(root.getAttribute("data-run-status")).toBe("idle");
    });
    expect(screen.queryByTestId("run-error-banner")).toBeNull();
    expect(screen.getByTestId("thread-canvas")).not.toBeNull();
    // No run → no SSE subscription opened.
    expect(transport.subs).toHaveLength(0);
  });

  // === PR-3.6 — tabbed right rail wiring ===

  it("mounts the tabbed right rail (Chat default) and collapses the in-canvas mode switcher", () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages") ? { messages: [] } : runningRun("Goal");
    renderRun(transport, makeStore());

    // The recomposed rail mounts inside the canvas chat column…
    const rail = screen.getByTestId("run-workspace-rail");
    expect(rail).not.toBeNull();
    expect(
      screen.getByRole("tablist", { name: "Run workspace tabs" }),
    ).not.toBeNull();
    // …Chat is the default tab and hosts the single TcChat instance…
    expect(
      screen.getByRole("tab", { name: "Chat" }).getAttribute("aria-selected"),
    ).toBe("true");
    expect(
      within(screen.getByTestId("run-rail-panel-chat")).getByTestId("tc-chat"),
    ).not.toBeNull();
    // …and there is exactly ONE TcChat (rail owns the column, not ThreadCanvas).
    expect(screen.getAllByTestId("tc-chat")).toHaveLength(1);
    // RunHeader is the single mode control — the in-canvas switcher is gone.
    expect(screen.getByTestId("run-mode-switcher")).not.toBeNull();
    expect(screen.queryByTestId("tc-mode-switcher")).toBeNull();
  });

  it("toggling mode keeps the same rail + chat surface (single-mount, FR-3.9/3.13)", () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages") ? { messages: [] } : runningRun("Goal");
    renderRun(transport, makeStore());

    const railBefore = screen.getByTestId("run-workspace-rail");
    const chatBefore = screen.getByTestId("tc-chat");

    fireEvent.click(screen.getByTestId("run-mode-focus"));

    expect(
      screen.getByTestId("run-destination").getAttribute("data-mode"),
    ).toBe("focus");
    // Same DOM nodes survive the mode switch — no remount.
    expect(screen.getByTestId("run-workspace-rail")).toBe(railBefore);
    expect(screen.getByTestId("tc-chat")).toBe(chatBefore);
    // Focus collapses the rail to Chat-only: its tab chrome is suppressed.
    expect(
      screen.queryByRole("tablist", { name: "Run workspace tabs" }),
    ).toBeNull();
  });
});

// === PR-3.8 — parallel subagents: fleet card + lanes + Agents count ===
//
// Integration: a scripted RuntimeEventEnvelope[] with a fleet dispatch drives
// ALL THREE subagent views off the ONE canonical stream (FR-3.17) — the inline
// fleet card (a), one timeline lane per subagent (b), and the Agents "N live"
// count (c). Completion updates counts without remounting sibling lanes.

let seqCounter = 0;

function event(overrides: Record<string, unknown>): Record<string, unknown> {
  seqCounter += 1;
  return {
    event_id: `e-${seqCounter}`,
    run_id: "run-1",
    conversation_id: "conv-1",
    sequence_no: seqCounter,
    event_type: "progress",
    activity_kind: "event",
    payload: {},
    created_at: new Date(1716000000000 + seqCounter * 1000).toISOString(),
    ...overrides,
  };
}

function fleetStarted(): Record<string, unknown> {
  return event({
    event_type: "subagent_fleet_started",
    source: "main_agent",
    activity_kind: "subagent",
    payload: {
      fleet_id: "fleet-1",
      title: "Parallel research",
      agent_ids: ["doc_reader", "press_scout"],
    },
  });
}

function subagentStarted(
  taskId: string,
  subagentId: string,
): Record<string, unknown> {
  return event({
    event_type: "subagent_started",
    source: "subagent",
    activity_kind: "subagent",
    task_id: taskId,
    subagent_id: subagentId,
    payload: { parent_fleet_id: "fleet-1", subagent_name: subagentId },
  });
}

describe("RunDestination — parallel subagents (PR-3.8 / FR-3.17)", () => {
  it("renders the fleet card, per-subagent lanes, and Agents 'N live' from one stream", async () => {
    seqCounter = 0;
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages")
        ? { messages: [] }
        : runningRun("Fan out the research");
    renderRun(transport, makeStore());

    // The session tail and the swimlane both subscribe to the same run stream.
    await waitFor(() => expect(transport.sessionSub).toBeDefined());
    await waitFor(() => expect(transport.swimlaneSub).toBeDefined());

    act(() => {
      transport.emit(fleetStarted());
      transport.emit(subagentStarted("task_alpha", "doc_reader"));
      transport.emit(subagentStarted("task_beta", "press_scout"));
    });

    // (a) inline SubagentFleetCard in the conversation…
    const card = await screen.findByTestId("tc-chat-fleet-fleet-1");
    expect(card).toHaveTextContent("Dispatched 2 subagents in parallel");
    // (b) one live timeline lane per subagent…
    expect(
      screen.getByTestId("tc-swimlanes-lane-subagent:doc_reader"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("tc-swimlanes-lane-subagent:press_scout"),
    ).toBeInTheDocument();
    // (c) live Agents-tab count — all from the SINGLE stream.
    expect(screen.getByTestId("run-rail-agents-badge")).toHaveTextContent(
      "2 live",
    );
  });

  it("updates the count on completion without remounting sibling lanes (FR-3.9)", async () => {
    seqCounter = 0;
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages")
        ? { messages: [] }
        : runningRun("Fan out the research");
    renderRun(transport, makeStore());

    await waitFor(() => expect(transport.sessionSub).toBeDefined());
    await waitFor(() => expect(transport.swimlaneSub).toBeDefined());

    act(() => {
      transport.emit(fleetStarted());
      transport.emit(subagentStarted("task_alpha", "doc_reader"));
      transport.emit(subagentStarted("task_beta", "press_scout"));
    });

    await screen.findByTestId("tc-chat-fleet-fleet-1");
    const survivingLane = screen.getByTestId(
      "tc-swimlanes-lane-subagent:press_scout",
    );
    expect(screen.getByTestId("run-rail-agents-badge")).toHaveTextContent(
      "2 live",
    );

    act(() => {
      transport.emit(
        event({
          event_type: "subagent_completed",
          source: "subagent",
          activity_kind: "subagent",
          task_id: "task_alpha",
          subagent_id: "doc_reader",
          status: "completed",
          payload: { parent_fleet_id: "fleet-1" },
        }),
      );
    });

    // One still running → badge drops to "1 live"…
    await waitFor(() =>
      expect(screen.getByTestId("run-rail-agents-badge")).toHaveTextContent(
        "1 live",
      ),
    );
    // …and the still-running subagent's lane is the SAME node (no remount).
    expect(screen.getByTestId("tc-swimlanes-lane-subagent:press_scout")).toBe(
      survivingLane,
    );
  });
});
