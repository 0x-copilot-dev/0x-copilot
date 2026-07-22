// RunDestination — cockpit-shell composition tests (PR-3.5).
//
// The shell is exercised through the same port fakes the pieces use: a
// Transport that resolves the conversation head (`latest_run_id`) + captures SSE
// subscriptions, and a Map-backed KeyValueStore for the Studio/Focus mode. The
// assertions cover the
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
import { describe, expect, it, vi } from "vitest";

import type { ConversationId, RunId } from "@0x-copilot/api-types";
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
import { RunDestination, buildRunCreateBody } from "./RunDestination";
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
  readonly onMessage?: (raw: string) => void;
  readonly onError?: (err: Error) => void;
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
      onMessage: opts.onMessage,
      onError: opts.onError,
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

// A conversation-head projection whose current (live) run is run-1
// (desktop-run-identity §D2). The cockpit resolves the run from the head field
// `latest_run_id`; `runs`/`goal` are carried for readability but are IGNORED by
// the source — `session.runs` stays empty and the header derives "Untitled run"
// until the runs-list endpoint lands (Phase 6).
function runningRun(goal: string) {
  return {
    latest_run_id: "run-1",
    latest_run_id_any_status: "run-1",
    runs: [{ run_id: "run-1", status: "running", goal }],
  };
}

/**
 * A state-changing, surface-touching runtime event — the projector turns it
 * into one timeline bead (lane = the uri scheme) that the mini-timeline can
 * scrub to. Shaped to pass `isRuntimeEventEnvelope` (the session-stream guard).
 */
function surfaceEvent(
  sequenceNo: number,
  eventId: string,
  surfaceUri: string,
  createdAt: string,
) {
  return {
    event_id: eventId,
    run_id: "run-1",
    conversation_id: "conv-1",
    sequence_no: sequenceNo,
    event_type: "tool_result",
    activity_kind: "tool",
    payload: { surface_uri: surfaceUri },
    created_at: createdAt,
  };
}

/** Push scripted events into the session's (`runtime_event`) SSE tail. */
function streamSessionEvents(
  transport: FakeTransport,
  events: readonly ReturnType<typeof surfaceEvent>[],
): void {
  act(() => {
    for (const event of events) {
      transport.sessionSub?.onMessage?.(JSON.stringify(event));
    }
  });
}

describe("RunDestination — shell composition", () => {
  it("renders the run header (kicker + goal) and the thread canvas from the session", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages")
        ? { messages: [] }
        : runningRun("Ship the renewal batch");

    renderRun(transport, makeStore());

    // Header mounts immediately; the canvas mounts once the run resolves (idle
    // shows the empty-state composer until then — PR-3.11).
    expect(screen.getByTestId("run-header")).not.toBeNull();
    await waitFor(() =>
      expect(screen.getByTestId("thread-canvas")).not.toBeNull(),
    );
    expect(screen.getByTestId("run-header-kicker").textContent).toBe(
      "ACTIVE RUN",
    );
    // The head field carries only a run id (no goal), so a head-resolved run
    // shows the honest generic title — never idle STANDBY. The run's real goal
    // arrives with the runs-list in Phase 6.
    await waitFor(() =>
      expect(screen.getByTestId("run-header-goal").textContent).toBe(
        "Untitled run",
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
    const canvas = await screen.findByTestId("thread-canvas");
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

  it("renders the empty/idle goal composer (not a blank canvas) when there is no run — PR-3.11/FR-3.25", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages") ? { messages: [] } : { runs: [] };
    renderRun(transport, makeStore());

    await waitFor(() => {
      const root = screen.getByTestId("run-destination");
      expect(root.getAttribute("data-run-status")).toBe("idle");
    });
    // Empty/idle → the goal composer, NOT a ThreadCanvas or a placeholder.
    expect(screen.getByTestId("run-empty-state")).not.toBeNull();
    expect(screen.queryByTestId("thread-canvas")).toBeNull();
    expect(screen.queryByTestId("run-error-banner")).toBeNull();
    // ≤1 run → no multi-run selector chrome.
    expect(screen.queryByTestId("run-multi-select")).toBeNull();
    // No run → no SSE subscription opened.
    expect(transport.subs).toHaveLength(0);
  });

  // === PR-3.6 — tabbed right rail wiring ===

  it("mounts the tabbed right rail (Chat default) and collapses the in-canvas mode switcher", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages") ? { messages: [] } : runningRun("Goal");
    renderRun(transport, makeStore());

    // The recomposed rail mounts inside the canvas chat column once the run
    // resolves (idle shows the empty-state composer — PR-3.11)…
    const rail = await screen.findByTestId("run-workspace-rail");
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

  it("toggling mode keeps the same rail + chat surface (single-mount, FR-3.9/3.13)", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages") ? { messages: [] } : runningRun("Goal");
    renderRun(transport, makeStore());

    const railBefore = await screen.findByTestId("run-workspace-rail");
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

  // === PR-3.7 — timeline scrub ↔ surface time-travel + snap-to-now ===

  async function renderScrubbable(): Promise<FakeTransport> {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages") ? { messages: [] } : runningRun("Goal");
    renderRun(transport, makeStore());
    // The session resolves the run, then opens its `runtime_event` tail.
    await waitFor(() => expect(transport.sessionSub).toBeDefined());
    streamSessionEvents(transport, [
      surfaceEvent(1, "e1", "email://draft-1", "2026-05-17T10:00:00.000Z"),
      surfaceEvent(2, "e2", "sheet://row-2", "2026-05-17T10:05:00.000Z"),
    ]);
    return transport;
  }

  function composerTextarea(): HTMLTextAreaElement {
    return screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
  }

  it("scrubbing a bead shows the Viewing banner, disables the composer, hides approvals, and snaps the surface tab (FR-3.15)", async () => {
    await renderScrubbable();

    // Live: no banner, Approvals tab present, composer enabled.
    expect(screen.queryByTestId("run-viewing-banner")).toBeNull();
    expect(screen.getByRole("tab", { name: "Approvals" })).not.toBeNull();
    expect(composerTextarea().disabled).toBe(false);

    // Scrub to the first (email) bead via the mini-timeline.
    fireEvent.click(screen.getByTestId("tc-mini-timeline-bead-e1"));

    // Viewing banner appears, with a "Return to live" affordance.
    const banner = screen.getByTestId("run-viewing-banner");
    expect(banner.getAttribute("role")).toBe("status");
    expect(screen.getByTestId("run-viewing-label").textContent).toContain(
      "Viewing",
    );
    expect(screen.getByTestId("run-return-to-live")).not.toBeNull();

    // Composer is disabled (via the SwimlaneScrubProvider → TcChat ghost).
    expect(composerTextarea().disabled).toBe(true);
    expect(screen.getByTestId("tc-chat").getAttribute("data-ghost")).toBe(
      "true",
    );

    // Approvals tab is hidden — you cannot approve a past state.
    expect(screen.queryByRole("tab", { name: "Approvals" })).toBeNull();
    expect(
      screen
        .getByTestId("run-workspace-rail")
        .getAttribute("data-approvals-hidden"),
    ).toBe("true");

    // The active surface tab snapped to the scrubbed bead's surface.
    const activeTab = screen
      .getByTestId("tc-tabs")
      .querySelector('[data-active="true"]');
    expect(activeTab?.getAttribute("data-uri")).toBe("email://draft-1");
  });

  it("Return to live clears the banner and re-enables the composer + approvals (FR-3.16)", async () => {
    await renderScrubbable();
    fireEvent.click(screen.getByTestId("tc-mini-timeline-bead-e1"));
    expect(screen.getByTestId("run-viewing-banner")).not.toBeNull();

    fireEvent.click(screen.getByTestId("run-return-to-live"));

    expect(screen.queryByTestId("run-viewing-banner")).toBeNull();
    expect(composerTextarea().disabled).toBe(false);
    expect(screen.getByTestId("tc-chat").getAttribute("data-ghost")).toBe(
      "false",
    );
    expect(screen.getByRole("tab", { name: "Approvals" })).not.toBeNull();
    expect(
      screen
        .getByTestId("run-workspace-rail")
        .getAttribute("data-approvals-hidden"),
    ).toBe("false");
  });

  it("⌘L / Escape on the timeline snaps to now (clears the banner)", async () => {
    await renderScrubbable();
    fireEvent.click(screen.getByTestId("tc-mini-timeline-bead-e2"));
    expect(screen.getByTestId("run-viewing-banner")).not.toBeNull();

    // ⌘L on the mini-timeline dispatches snap-to-now up to the shell.
    fireEvent.keyDown(screen.getByTestId("tc-mini-timeline"), {
      key: "l",
      metaKey: true,
    });
    expect(screen.queryByTestId("run-viewing-banner")).toBeNull();
    expect(composerTextarea().disabled).toBe(false);

    // Scrub again, then snap via Escape.
    fireEvent.click(screen.getByTestId("tc-mini-timeline-bead-e2"));
    expect(screen.getByTestId("run-viewing-banner")).not.toBeNull();
    fireEvent.keyDown(screen.getByTestId("tc-mini-timeline"), {
      key: "Escape",
    });
    expect(screen.queryByTestId("run-viewing-banner")).toBeNull();
  });

  it("scrubbing does not remount the chat/composer (single-mount invariant, FR-3.9)", async () => {
    await renderScrubbable();
    const chatBefore = screen.getByTestId("tc-chat");
    const composerBefore = composerTextarea();

    fireEvent.click(screen.getByTestId("tc-mini-timeline-bead-e1"));
    fireEvent.click(screen.getByTestId("run-return-to-live"));

    // Same DOM nodes survive the scrub → snap round-trip (no remount).
    expect(screen.getByTestId("tc-chat")).toBe(chatBefore);
    expect(composerTextarea()).toBe(composerBefore);
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

// === PR-3.10 — approvals: in-chat ApprovalCard + rail count + resolution ===
//
// Integration: a scripted `approval_requested` event surfaces the in-chat
// ApprovalCard AND the Approvals-tab pending badge from the ONE canonical
// stream (FR-3.22/3.12). Approve/Reject in chat optimistically flips the card
// to a receipt, drops the count, and POSTs the decision through the Transport
// port. Approvals are hidden while scrubbed off-now (FR-3.15).

function approvalRequested(approvalId: string): Record<string, unknown> {
  return event({
    event_type: "approval_requested",
    activity_kind: "approval",
    payload: {
      approval_id: approvalId,
      approval_kind: "tool_action",
      display_name: "Post to #launch-aurora",
      tool_name: "slack_post_message",
      message: "Posts the launch note to #launch-aurora",
      server_name: "SLACK",
      read_only: false,
      arguments: { channel: "#launch-aurora" },
    },
  });
}

function approvalResolved(
  approvalId: string,
  decision: "approved" | "rejected",
): Record<string, unknown> {
  return event({
    event_type: "approval_resolved",
    activity_kind: "approval",
    payload: { approval_id: approvalId, decision, status: decision },
  });
}

describe("RunDestination — approvals (PR-3.10 / FR-3.21/3.22)", () => {
  async function renderWithApproval(): Promise<FakeTransport> {
    seqCounter = 0;
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages")
        ? { messages: [] }
        : runningRun("Post the launch note");
    renderRun(transport, makeStore());
    await waitFor(() => expect(transport.sessionSub).toBeDefined());
    act(() => {
      transport.emit(approvalRequested("appr-1"));
    });
    await screen.findByTestId("tc-chat-approval-appr-1");
    return transport;
  }

  it("surfaces the in-chat ApprovalCard + the Approvals-tab count from one stream", async () => {
    await renderWithApproval();

    // The 4-zone ApprovalCard renders the pending approval, with Approve/Reject.
    const card = screen.getByTestId("tc-chat-approval-appr-1");
    expect(card).toHaveTextContent("Post to #launch-aurora");
    expect(
      screen.getByTestId("tc-chat-approval-approve-appr-1"),
    ).not.toBeNull();
    expect(screen.getByTestId("tc-chat-approval-reject-appr-1")).not.toBeNull();
    // …and the Approvals tab shows the accent pending count badge (FR-3.12).
    expect(screen.getByTestId("run-rail-approvals-badge")).toHaveTextContent(
      "1",
    );
  });

  it("approving in chat flips the card to a signed receipt, clears the count, and POSTs the decision", async () => {
    const transport = await renderWithApproval();

    act(() => {
      fireEvent.click(screen.getByTestId("tc-chat-approval-approve-appr-1"));
    });

    // Optimistic: card → receipt (approved); pending card + badge gone.
    await waitFor(() =>
      expect(
        screen.getByTestId("tc-chat-approval-receipt-appr-1"),
      ).toHaveAttribute("data-decision", "approved"),
    );
    expect(screen.queryByTestId("tc-chat-approval-appr-1")).toBeNull();
    expect(screen.queryByTestId("run-rail-approvals-badge")).toBeNull();
    // The host POSTed the decision through the Transport port (host owns POST).
    await waitFor(() =>
      expect(
        transport.requests.some(
          (r) =>
            r.method === "POST" &&
            r.path === "/v1/agent/approvals/appr-1/decision",
        ),
      ).toBe(true),
    );
  });

  it("rejecting in chat flips the card to a rejected receipt", async () => {
    await renderWithApproval();

    act(() => {
      fireEvent.click(screen.getByTestId("tc-chat-approval-reject-appr-1"));
    });

    await waitFor(() =>
      expect(
        screen.getByTestId("tc-chat-approval-receipt-appr-1"),
      ).toHaveAttribute("data-decision", "rejected"),
    );
    expect(screen.queryByTestId("tc-chat-approval-appr-1")).toBeNull();
  });

  it("reconciles the server `approval_resolved` frame into a receipt", async () => {
    const transport = await renderWithApproval();

    act(() => {
      transport.emit(approvalResolved("appr-1", "approved"));
    });

    await waitFor(() =>
      expect(
        screen.getByTestId("tc-chat-approval-receipt-appr-1"),
      ).toHaveAttribute("data-decision", "approved"),
    );
    expect(screen.queryByTestId("tc-chat-approval-appr-1")).toBeNull();
  });

  it("hides in-chat approvals + the count while scrubbed off-now, restoring on snap-to-now (FR-3.15)", async () => {
    seqCounter = 0;
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages") ? { messages: [] } : runningRun("Goal");
    renderRun(transport, makeStore());
    await waitFor(() => expect(transport.sessionSub).toBeDefined());

    act(() => {
      // Distinct sequence numbers — the session tail dedupes by `sequence_no`.
      transport.emit(approvalRequested("appr-1")); // seq 1 (via event())
      transport.emit(
        surfaceEvent(9, "bead-1", "sheet://row-2", "2026-05-17T10:00:00.000Z"),
      );
    });

    // Live: approval card + rail badge present.
    await screen.findByTestId("tc-chat-approval-appr-1");
    expect(screen.getByTestId("run-rail-approvals-badge")).toHaveTextContent(
      "1",
    );

    // Scrub to the bead → approvals hidden (cannot approve a past state).
    act(() => {
      fireEvent.click(screen.getByTestId("tc-mini-timeline-bead-bead-1"));
    });
    expect(screen.queryByTestId("tc-chat-approval-appr-1")).toBeNull();
    expect(screen.queryByTestId("run-rail-approvals-badge")).toBeNull();

    // Snap back to now → approvals restored.
    act(() => {
      fireEvent.click(screen.getByTestId("run-return-to-live"));
    });
    expect(screen.getByTestId("tc-chat-approval-appr-1")).not.toBeNull();
    expect(screen.getByTestId("run-rail-approvals-badge")).toHaveTextContent(
      "1",
    );
  });
});

// === PR-3.11 — empty/idle goal composer + multi-run selection ===
//
// Integration: with no run the shell mounts the empty-state goal composer
// (FR-3.25); submitting a goal POSTs a run and binds it via the `runId` seam, so
// the live cockpit mounts IN PLACE (the shell root node is unchanged). With >1
// run the shell mounts the run selector (FR-3.26); picking a run rebinds the
// session's SSE tail without remounting the ThreadCanvas.

// A conversation head whose current (live) run is run-a. `runs` is carried for
// readability only — the source binds run-a from `latest_run_id` and keeps
// `session.runs` empty this phase (the runs-list + RunMultiSelect data source
// lands in Phase 6).
const TWO_RUNS = {
  latest_run_id: "run-a",
  latest_run_id_any_status: "run-a",
  runs: [
    {
      run_id: "run-a",
      status: "running",
      goal: "Ship the renewal batch",
      started_at: "2026-05-17T10:00:00.000Z",
    },
    {
      run_id: "run-b",
      status: "completed",
      goal: "Reconcile Q2 invoices",
      started_at: "2026-05-17T09:00:00.000Z",
    },
  ],
};

describe("RunDestination — empty/idle + multi-run (PR-3.11 / FR-3.25/3.26)", () => {
  it("starts a run from the empty composer and binds it live WITHOUT remounting the shell (FR-3.25)", async () => {
    const transport = new FakeTransport();
    // No runs listed → empty state; POST creates `run-new`.
    transport.requestHandler = async (req) => {
      if (req.path.includes("/messages")) {
        return { messages: [] };
      }
      if (req.method === "POST" && req.path === "/v1/agent/runs") {
        return { run_id: "run-new" };
      }
      return { runs: [] };
    };
    renderRun(transport, makeStore());

    // Empty/idle: the goal composer, no ThreadCanvas.
    await screen.findByTestId("run-empty-state");
    expect(screen.queryByTestId("thread-canvas")).toBeNull();
    const rootBefore = screen.getByTestId("run-destination");

    // Give it a goal and start.
    fireEvent.change(screen.getByTestId("run-empty-goal-input"), {
      target: { value: "Draft the launch note" },
    });
    act(() => {
      fireEvent.click(screen.getByTestId("run-empty-submit"));
    });

    // Empty → live: the ThreadCanvas mounts in place, the composer is gone…
    await screen.findByTestId("thread-canvas");
    expect(screen.queryByTestId("run-empty-state")).toBeNull();
    // …and the SHELL root is the SAME DOM node (no host/shell remount).
    expect(screen.getByTestId("run-destination")).toBe(rootBefore);

    // The freshly-started run drives the session's SSE tail.
    await waitFor(() =>
      expect(transport.sessionSub?.path).toBe("/v1/agent/runs/run-new/stream"),
    );
    // The shell POSTed the run through the Transport port (identity from token).
    expect(
      transport.requests.some(
        (r) => r.method === "POST" && r.path === "/v1/agent/runs",
      ),
    ).toBe(true);
  });

  it("uses the host `onStartRun` when provided (host owns run creation)", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages") ? { messages: [] } : { runs: [] };
    const onStartRun = vi.fn(async () => "host-run");

    render(
      <TransportProvider transport={transport}>
        <KeyValueStoreProvider store={makeStore()}>
          <RunDestination conversationId={CONV} onStartRun={onStartRun} />
        </KeyValueStoreProvider>
      </TransportProvider>,
    );

    await screen.findByTestId("run-empty-state");
    fireEvent.change(screen.getByTestId("run-empty-goal-input"), {
      target: { value: "Do the thing" },
    });
    act(() => {
      fireEvent.click(screen.getByTestId("run-empty-submit"));
    });

    await screen.findByTestId("thread-canvas");
    // The plain fallback composer sends a bare goal, wrapped into the shared
    // RunStartRequest seam (no model/attachments from the plain box).
    expect(onStartRun).toHaveBeenCalledWith({ goal: "Do the thing" });
    // The host callback supplied the run id — the shell did NOT POST itself.
    expect(
      transport.requests.some(
        (r) => r.method === "POST" && r.path === "/v1/agent/runs",
      ),
    ).toBe(false);
    await waitFor(() =>
      expect(transport.sessionSub?.path).toBe("/v1/agent/runs/host-run/stream"),
    );
  });

  it("surfaces a rejected start's missing-key config error as an 'Add a provider key' CTA that routes to provider-key settings (no silent dead end)", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages") ? { messages: [] } : { runs: [] };
    const onOpenModelSettings = vi.fn();
    // The keyless dead end: the host run-create rejects with the facade
    // `configuration_error` envelope. The shell must parse the actionable
    // `safe_message` + `code` out of it and guide the user to Provider keys —
    // NOT sit on the composer with no feedback (the confirmed-live dead end).
    const onStartRun = vi.fn(() =>
      Promise.reject(
        new Error(
          JSON.stringify({
            detail: {
              code: "configuration_error",
              safe_message:
                "Missing API key for model provider 'openai'. Add one in Settings -> Provider keys.",
              correlation_id: "cid-1",
            },
          }),
        ),
      ),
    );

    render(
      <TransportProvider transport={transport}>
        <KeyValueStoreProvider store={makeStore()}>
          <RunDestination
            conversationId={CONV}
            onStartRun={onStartRun}
            onOpenModelSettings={onOpenModelSettings}
          />
        </KeyValueStoreProvider>
      </TransportProvider>,
    );

    await screen.findByTestId("run-empty-state");
    fireEvent.change(screen.getByTestId("run-empty-goal-input"), {
      target: { value: "Draft the launch note" },
    });
    act(() => {
      fireEvent.click(screen.getByTestId("run-empty-submit"));
    });

    // The rejected start does NOT flip to a live canvas — the empty state stays,
    // now carrying the actionable safe_message (never the raw JSON envelope).
    const message = await screen.findByTestId("run-empty-error-message");
    expect(message.textContent).toContain(
      "Missing API key for model provider 'openai'",
    );
    expect(message.textContent).not.toContain("{");
    expect(screen.queryByTestId("thread-canvas")).toBeNull();

    // The config-error CTA opens Settings → Provider keys (the onboarding path).
    fireEvent.click(screen.getByTestId("run-empty-error-cta"));
    expect(onOpenModelSettings).toHaveBeenCalledTimes(1);
  });

  // -------------------------------------------------------------------------
  // renderEmptyComposer — the design's "What should we run first?" rich empty
  // composer slot (host mounts OnboardingComposer; the shell keeps the seam).
  // -------------------------------------------------------------------------

  it("mounts the injected rich empty composer (not the plain goal card) when there is no active run", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages") ? { messages: [] } : { runs: [] };

    render(
      <TransportProvider transport={transport}>
        <KeyValueStoreProvider store={makeStore()}>
          <RunDestination
            conversationId={CONV}
            renderEmptyComposer={() => (
              <div data-testid="rich-empty-composer">
                What should we run first?
              </div>
            )}
          />
        </KeyValueStoreProvider>
      </TransportProvider>,
    );

    // The design composer renders; the plain fallback card does NOT.
    await screen.findByTestId("rich-empty-composer");
    expect(screen.queryByTestId("run-empty-state")).toBeNull();
    expect(screen.queryByTestId("thread-canvas")).toBeNull();
  });

  it("the rich composer's onStartRun forwards the full payload and binds empty→live without a shell remount", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages") ? { messages: [] } : { runs: [] };
    const onStartRun = vi.fn(async () => "rich-run");
    const richPayload = {
      goal: "Watch my wallet",
      model: { provider: "anthropic", model_name: "claude-sonnet-4-5" },
      attachments: [
        {
          id: "att-1",
          type: "file",
          name: "airdrop-claims.csv",
          content: [],
        },
      ],
      webSearchEnabled: false,
    };

    render(
      <TransportProvider transport={transport}>
        <KeyValueStoreProvider store={makeStore()}>
          <RunDestination
            conversationId={CONV}
            onStartRun={onStartRun}
            renderEmptyComposer={(ctx) => (
              <button
                type="button"
                data-testid="rich-send"
                onClick={() => ctx.onStartRun(richPayload)}
              >
                Send
              </button>
            )}
          />
        </KeyValueStoreProvider>
      </TransportProvider>,
    );

    await screen.findByTestId("rich-send");
    const rootBefore = screen.getByTestId("run-destination");
    act(() => {
      fireEvent.click(screen.getByTestId("rich-send"));
    });

    // Empty → live in place (same shell DOM node), driven by the rich payload.
    await screen.findByTestId("thread-canvas");
    expect(screen.getByTestId("run-destination")).toBe(rootBefore);
    // The host onStartRun received the rich selection (goal trimmed + model +
    // attachments + web-search toggle) — not a bare string.
    expect(onStartRun).toHaveBeenCalledWith(richPayload);
    await waitFor(() =>
      expect(transport.sessionSub?.path).toBe("/v1/agent/runs/rich-run/stream"),
    );
  });

  it("shows the readiness setup notice (with CTA) alongside the rich composer when no model is configured", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages") ? { messages: [] } : { runs: [] };
    const onOpenModelSettings = vi.fn();

    render(
      <TransportProvider transport={transport}>
        <KeyValueStoreProvider store={makeStore()}>
          <RunDestination
            conversationId={CONV}
            modelReady={false}
            onOpenModelSettings={onOpenModelSettings}
            renderEmptyComposer={(ctx) => (
              <div data-testid="rich-empty-composer">
                {String(ctx.modelReady)}
              </div>
            )}
          />
        </KeyValueStoreProvider>
      </TransportProvider>,
    );

    // The composer still renders (design frame), but a "Set up your model"
    // notice explains why it's inert and routes to Provider keys.
    await screen.findByTestId("rich-empty-composer");
    expect(screen.getByTestId("rich-empty-composer").textContent).toBe("false");
    await screen.findByTestId("run-empty-setup");
    fireEvent.click(screen.getByTestId("run-empty-setup-cta"));
    expect(onOpenModelSettings).toHaveBeenCalledTimes(1);
  });

  it("hides the setup notice once a model is ready", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages") ? { messages: [] } : { runs: [] };

    render(
      <TransportProvider transport={transport}>
        <KeyValueStoreProvider store={makeStore()}>
          <RunDestination
            conversationId={CONV}
            modelReady
            renderEmptyComposer={() => (
              <div data-testid="rich-empty-composer">ready</div>
            )}
          />
        </KeyValueStoreProvider>
      </TransportProvider>,
    );

    await screen.findByTestId("rich-empty-composer");
    expect(screen.queryByTestId("run-empty-setup")).toBeNull();
  });

  it("claims an attachment-only start with an honest 'Untitled run' header, never idle STANDBY", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages") ? { messages: [] } : { runs: [] };
    const onStartRun = vi.fn(async () => "att-run");

    render(
      <TransportProvider transport={transport}>
        <KeyValueStoreProvider store={makeStore()}>
          <RunDestination
            conversationId={CONV}
            onStartRun={onStartRun}
            renderEmptyComposer={(ctx) => (
              <button
                type="button"
                data-testid="att-send"
                onClick={() =>
                  ctx.onStartRun({
                    goal: "",
                    attachments: [
                      { id: "a1", type: "file", name: "x.csv", content: [] },
                    ],
                  })
                }
              >
                Send
              </button>
            )}
          />
        </KeyValueStoreProvider>
      </TransportProvider>,
    );

    await screen.findByTestId("att-send");
    act(() => {
      fireEvent.click(screen.getByTestId("att-send"));
    });

    // The attachment-only run is accepted (goal-less send is not a no-op) and
    // the header claims it — a generic title + ACTIVE kicker, not the idle lie.
    await screen.findByTestId("thread-canvas");
    expect(onStartRun).toHaveBeenCalledWith({
      goal: "",
      attachments: [{ id: "a1", type: "file", name: "x.csv", content: [] }],
    });
    await waitFor(() =>
      expect(screen.getByTestId("run-header-goal").textContent).toBe(
        "Untitled run",
      ),
    );
    expect(screen.getByTestId("run-header-kicker").textContent).toBe(
      "ACTIVE RUN",
    );
  });

  it("auto-binds the conversation's head (live) run and populates the multi-run selector (Phase 6)", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages") ? { messages: [] } : TWO_RUNS;
    renderRun(transport, makeStore());

    // The head resolves the live run (run-a); the live cockpit renders (not the
    // empty composer) and binds run-a's SSE tail.
    await screen.findByTestId("thread-canvas");
    await waitFor(() =>
      expect(transport.sessionSub?.path).toBe("/v1/agent/runs/run-a/stream"),
    );
    expect(screen.queryByTestId("run-empty-state")).toBeNull();
    // Phase 6: the runs-list endpoint populates `session.runs`, so the multi-run
    // selector now renders (this conversation has two runs).
    await waitFor(() =>
      expect(screen.queryByTestId("run-multi-select")).not.toBeNull(),
    );
  });

  it("rebinds the session's SSE tail to another run via the runId seam without remounting the canvas (FR-3.26)", async () => {
    // The runs-list-backed RunMultiSelect UI is inert this phase (session.runs is
    // empty), so multi-run selection is exercised through the ONE `boundRunId`
    // sink directly — here the `runId` deep-link seam, the same setter
    // `selectRun`/`bindRun`/head all funnel through (§D3). This protects the
    // shell-level invariant: rebinding the active run rebinds the stream WITHOUT
    // remounting the ThreadCanvas.
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages") ? { messages: [] } : { runs: [] };
    const store = makeStore();

    const view = render(
      <TransportProvider transport={transport}>
        <KeyValueStoreProvider store={store}>
          <RunDestination conversationId={CONV} runId={"run-a" as RunId} />
        </KeyValueStoreProvider>
      </TransportProvider>,
    );

    await screen.findByTestId("thread-canvas");
    await waitFor(() =>
      expect(transport.sessionSub?.path).toBe("/v1/agent/runs/run-a/stream"),
    );
    const canvasBefore = screen.getByTestId("thread-canvas");
    const runASub = transport.sessionSub;

    // Rebind to another run through the runId seam.
    view.rerender(
      <TransportProvider transport={transport}>
        <KeyValueStoreProvider store={store}>
          <RunDestination conversationId={CONV} runId={"run-b" as RunId} />
        </KeyValueStoreProvider>
      </TransportProvider>,
    );

    // The session rebinds to run-b's stream (a fresh sub; run-a's is closed)…
    await waitFor(() =>
      expect(transport.sessionSub?.path).toBe("/v1/agent/runs/run-b/stream"),
    );
    expect(runASub?.closed).toBe(true);
    // …and the ThreadCanvas is the SAME node — no gratuitous cockpit remount.
    expect(screen.getByTestId("thread-canvas")).toBe(canvasBefore);
  });

  it("shows no multi-run selector for a single run", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages")
        ? { messages: [] }
        : runningRun("Only run");
    renderRun(transport, makeStore());

    await screen.findByTestId("thread-canvas");
    expect(screen.queryByTestId("run-multi-select")).toBeNull();
  });
});

// === PRD-04 — surface tabs + on-surface diffs (auto-follow / pin / decisions) ===
//
// Integration: surface tool_results populate the surface-tab strip off the ONE
// canonical stream (FR-3.3); the center pane auto-follows the newest surface
// until the user pins one (a manual tab click). An approval carrying a surface
// diff renders the Approve/Reject controls over the diff; approving reuses the
// SAME resolveApproval machinery the in-chat card uses and POSTs the decision.
// Diff controls hide while scrubbed off-now (FR-3.15).

/** A `tool_result` carrying the PRD-01 `payload.surface` envelope. */
function surfaceToolResult(
  uri: string,
  id: string,
  data: Record<string, unknown> = {},
): Record<string, unknown> {
  return event({
    event_id: id,
    event_type: "tool_result",
    activity_kind: "tool",
    payload: {
      surface: { surface_uri: uri, archetype: "record", state: { data } },
    },
  });
}

/** An `approval_requested` proposing a diff on a surface. */
function surfaceDiffApproval(
  approvalId: string,
  uri: string,
): Record<string, unknown> {
  return event({
    event_type: "approval_requested",
    activity_kind: "approval",
    payload: {
      approval_id: approvalId,
      approval_kind: "tool_action",
      display_name: "Update the record",
      server_name: "LINEAR",
      surface: {
        surface_uri: uri,
        archetype: "record",
        state: { data: {} },
        diff: { changes: [{ field: "title", old: "a", new: "b" }] },
      },
    },
  });
}

function activeSurfaceTabUri(): string | null {
  return (
    screen
      .getByTestId("tc-tabs")
      .querySelector('[data-active="true"]')
      ?.getAttribute("data-uri") ?? null
  );
}

function surfaceTabCount(): number {
  return screen.getByTestId("tc-tabs").querySelectorAll('[role="tab"]').length;
}

describe("RunDestination — surface tabs + on-surface diffs (PRD-04)", () => {
  async function renderWithSession(): Promise<FakeTransport> {
    seqCounter = 0;
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages") ? { messages: [] } : runningRun("Goal");
    renderRun(transport, makeStore());
    await waitFor(() => expect(transport.sessionSub).toBeDefined());
    return transport;
  }

  it("auto-opens the newest surface as tabs stream in", async () => {
    const transport = await renderWithSession();
    act(() => {
      transport.emit(surfaceToolResult("record://a", "sa"));
      transport.emit(surfaceToolResult("record://b", "sb"));
    });
    await waitFor(() => expect(surfaceTabCount()).toBe(2));
    // Newest surface (b) auto-opens; no pin → no follow-live affordance.
    expect(activeSurfaceTabUri()).toBe("record://b");
    expect(screen.queryByTestId("run-follow-live-banner")).toBeNull();
  });

  it("pins on a manual tab click and un-pins via follow live", async () => {
    const transport = await renderWithSession();
    act(() => {
      transport.emit(surfaceToolResult("record://a", "sa"));
      transport.emit(surfaceToolResult("record://b", "sb"));
    });
    await waitFor(() => expect(activeSurfaceTabUri()).toBe("record://b"));

    // Click the older tab → pins it (active follows the click, not the newest).
    const olderTab = screen
      .getByTestId("tc-tabs")
      .querySelector('[data-uri="record://a"]') as HTMLElement;
    act(() => {
      fireEvent.click(olderTab);
    });
    expect(activeSurfaceTabUri()).toBe("record://a");
    // A newer surface exists → the "follow live" affordance appears.
    expect(
      screen.getByTestId("run-follow-live-banner").getAttribute("role"),
    ).toBe("status");

    // Follow live → un-pins; active snaps back to the newest surface.
    act(() => {
      fireEvent.click(screen.getByTestId("run-follow-live"));
    });
    expect(screen.queryByTestId("run-follow-live-banner")).toBeNull();
    expect(activeSurfaceTabUri()).toBe("record://b");
  });

  it("renders the on-surface diff controls and POSTs the decision on approve", async () => {
    const transport = await renderWithSession();
    act(() => {
      transport.emit(surfaceToolResult("record://seed/get_issue/1", "surf-1"));
      transport.emit(
        surfaceDiffApproval("appr-1", "record://seed/get_issue/1"),
      );
    });

    // The center pane shows the Approve/Reject/Suggest controls over the diff.
    await screen.findByTestId("tc-surface-mount-controls");

    act(() => {
      fireEvent.click(screen.getByTestId("tc-surface-mount-approve"));
    });

    // Optimistic: the diff clears (controls gone) — prop-driven, no internal state.
    await waitFor(() =>
      expect(screen.queryByTestId("tc-surface-mount-controls")).toBeNull(),
    );
    // The host POSTed the decision through the Transport port (reuses the SAME
    // resolveApproval machinery — diffId === approvalId).
    await waitFor(() =>
      expect(
        transport.requests.some(
          (r) =>
            r.method === "POST" &&
            r.path === "/v1/agent/approvals/appr-1/decision" &&
            (r.body as { decision?: string } | undefined)?.decision ===
              "approved",
        ),
      ).toBe(true),
    );
  });

  it("hides the on-surface diff controls while scrubbed off-now (FR-3.15)", async () => {
    const transport = await renderWithSession();
    act(() => {
      transport.emit(surfaceToolResult("record://seed/get_issue/1", "surf-1"));
      transport.emit(
        surfaceDiffApproval("appr-1", "record://seed/get_issue/1"),
      );
    });
    await screen.findByTestId("tc-surface-mount-controls");

    // Scrub to the surface's bead → the pending diff + its controls hide.
    act(() => {
      fireEvent.click(screen.getByTestId("tc-mini-timeline-bead-surf-1"));
    });
    expect(screen.getByTestId("run-viewing-banner")).not.toBeNull();
    expect(screen.queryByTestId("tc-surface-mount-controls")).toBeNull();

    // Snap back to now → controls restored.
    act(() => {
      fireEvent.click(screen.getByTestId("run-return-to-live"));
    });
    expect(screen.getByTestId("tc-surface-mount-controls")).not.toBeNull();
  });
});

// === PRD-09c — edit-on-surface overlay (Suggest changes → approve_with_edits) ===
//
// Integration: the on-surface "Suggest changes" control opens the host-owned
// EditOverlay OVER the pure adapter (ThreadCanvas.editSlot → TcSurfaceMount).
// The overlay's submit reuses the SAME resolveApproval POST machinery the plain
// approve/reject path uses, with `{ decision: "approve_with_edits", edits }`.
// Cancel returns to the pending diff (no POST). The plain approve path is
// unchanged (no `edits`).

/** An `approval_requested` proposing a MESSAGE-body diff on a `message://` surface. */
function messageDiffApproval(
  approvalId: string,
  uri: string,
): Record<string, unknown> {
  return event({
    event_type: "approval_requested",
    activity_kind: "approval",
    payload: {
      approval_id: approvalId,
      approval_kind: "tool_action",
      display_name: "Send the renewal email",
      server_name: "GMAIL",
      surface: {
        surface_uri: uri,
        archetype: "message",
        state: { data: {} },
        diff: {
          changes: [
            { field: "message.subject", old: "Renewal", new: "Renewal terms" },
            {
              field: "message.body",
              old: "Hi Jordan, the price holds.",
              new: "Hi Maya, the price holds.",
            },
          ],
        },
      },
    },
  });
}

/** An `approval_requested` proposing a RECORD field diff on a `record://` surface. */
function recordFieldDiffApproval(
  approvalId: string,
  uri: string,
): Record<string, unknown> {
  return event({
    event_type: "approval_requested",
    activity_kind: "approval",
    payload: {
      approval_id: approvalId,
      approval_kind: "tool_action",
      display_name: "Update the record",
      server_name: "LINEAR",
      surface: {
        surface_uri: uri,
        archetype: "record",
        state: { data: {} },
        diff: {
          changes: [
            { field: "title", old: "Old title", new: "New title" },
            { field: "priority", old: "P2", new: "P1" },
          ],
        },
      },
    },
  });
}

function decisionRequests(transport: FakeTransport, approvalId: string) {
  return transport.requests.filter(
    (r) =>
      r.method === "POST" &&
      r.path === `/v1/agent/approvals/${approvalId}/decision`,
  );
}

describe("RunDestination — edit-on-surface (PRD-09c)", () => {
  async function renderWithSession(): Promise<FakeTransport> {
    seqCounter = 0;
    const transport = new FakeTransport();
    transport.requestHandler = async (req) =>
      req.path.includes("/messages") ? { messages: [] } : runningRun("Goal");
    renderRun(transport, makeStore());
    await waitFor(() => expect(transport.sessionSub).toBeDefined());
    return transport;
  }

  it("opens the edit overlay from Suggest changes for a message archetype", async () => {
    const transport = await renderWithSession();
    act(() => {
      transport.emit(messageDiffApproval("appr-1", "message://gmail/send/1"));
    });
    await screen.findByTestId("tc-surface-mount-controls");

    // No overlay until the reviewer asks to edit.
    expect(screen.queryByTestId("surface-edit-overlay")).toBeNull();

    act(() => {
      fireEvent.click(screen.getByTestId("tc-surface-mount-suggest"));
    });

    // The message edit overlay mounts OVER the adapter…
    const overlay = await screen.findByTestId("surface-edit-overlay");
    expect(overlay.getAttribute("data-archetype")).toBe("message");
    expect(screen.getByTestId("message-edit-form")).not.toBeNull();
    // …seeded with the proposed body…
    expect(
      (screen.getByTestId("message-edit-body") as HTMLTextAreaElement).value,
    ).toBe("Hi Maya, the price holds.");
    // …and the bottom Approve/Reject/Suggest row is suppressed while editing.
    expect(screen.queryByTestId("tc-surface-mount-controls")).toBeNull();
    expect(
      screen.getByTestId("tc-surface-mount").getAttribute("data-editing"),
    ).toBe("true");
  });

  it("toggling a hunk excludes it and submit POSTs approve_with_edits with the edited body", async () => {
    const transport = await renderWithSession();
    act(() => {
      transport.emit(messageDiffApproval("appr-1", "message://gmail/send/1"));
    });
    await screen.findByTestId("tc-surface-mount-controls");
    act(() => {
      fireEvent.click(screen.getByTestId("tc-surface-mount-suggest"));
    });
    await screen.findByTestId("surface-edit-overlay");

    // Exclude the inserted hunk (h2) via the PRD-06 DiffText.
    act(() => {
      fireEvent.click(
        within(screen.getByTestId("message-edit-hunks")).getByTestId(
          "diff-insert",
        ),
      );
    });
    expect(
      screen
        .getByTestId("message-edit-hunk-status-h2")
        .getAttribute("data-accepted"),
    ).toBe("false");

    // Edit the body, then submit.
    act(() => {
      fireEvent.change(screen.getByTestId("message-edit-body"), {
        target: { value: "Hi Maya, price is locked." },
      });
    });
    act(() => {
      fireEvent.click(screen.getByTestId("surface-edit-submit"));
    });

    // The overlay closes and the diff clears optimistically (as approved).
    await waitFor(() =>
      expect(screen.queryByTestId("surface-edit-overlay")).toBeNull(),
    );
    await waitFor(() =>
      expect(screen.queryByTestId("tc-surface-mount-controls")).toBeNull(),
    );

    // Exactly one decision POST, carrying approve_with_edits + the edited body
    // and the kept-hunk subset (h2 excluded).
    await waitFor(() =>
      expect(decisionRequests(transport, "appr-1")).toHaveLength(1),
    );
    const body = decisionRequests(transport, "appr-1")[0].body as {
      decision: string;
      edits: {
        body?: string;
        accepted_hunk_ids?: string[];
      };
    };
    expect(body.decision).toBe("approve_with_edits");
    expect(body.edits.body).toBe("Hi Maya, price is locked.");
    expect(body.edits.accepted_hunk_ids).toEqual(["h1"]);
  });

  it("submits record field edits as approve_with_edits.fields", async () => {
    const transport = await renderWithSession();
    act(() => {
      transport.emit(
        recordFieldDiffApproval("appr-2", "record://linear/get_issue/1"),
      );
    });
    await screen.findByTestId("tc-surface-mount-controls");
    act(() => {
      fireEvent.click(screen.getByTestId("tc-surface-mount-suggest"));
    });
    const overlay = await screen.findByTestId("surface-edit-overlay");
    expect(overlay.getAttribute("data-archetype")).toBe("record");

    act(() => {
      fireEvent.change(screen.getByTestId("record-edit-field-title"), {
        target: { value: "Reviewed title" },
      });
    });
    act(() => {
      fireEvent.click(screen.getByTestId("surface-edit-submit"));
    });

    await waitFor(() =>
      expect(decisionRequests(transport, "appr-2")).toHaveLength(1),
    );
    const body = decisionRequests(transport, "appr-2")[0].body as {
      decision: string;
      edits: { fields?: Record<string, string> };
    };
    expect(body.decision).toBe("approve_with_edits");
    expect(body.edits.fields).toEqual({
      title: "Reviewed title",
      priority: "P1",
    });
  });

  it("cancel restores the pending diff and POSTs nothing", async () => {
    const transport = await renderWithSession();
    act(() => {
      transport.emit(messageDiffApproval("appr-3", "message://gmail/send/1"));
    });
    await screen.findByTestId("tc-surface-mount-controls");
    act(() => {
      fireEvent.click(screen.getByTestId("tc-surface-mount-suggest"));
    });
    await screen.findByTestId("surface-edit-overlay");

    act(() => {
      fireEvent.click(screen.getByTestId("surface-edit-cancel"));
    });

    // Overlay gone; the pending diff + its Approve/Reject/Suggest controls return.
    expect(screen.queryByTestId("surface-edit-overlay")).toBeNull();
    expect(screen.getByTestId("tc-surface-mount-controls")).not.toBeNull();
    expect(screen.getByTestId("tc-surface-mount-suggest")).not.toBeNull();
    // Cancel is inert — no decision POST fired.
    expect(decisionRequests(transport, "appr-3")).toHaveLength(0);
  });

  it("leaves the plain approve path unchanged (no edits payload)", async () => {
    const transport = await renderWithSession();
    act(() => {
      transport.emit(
        recordFieldDiffApproval("appr-4", "record://linear/get_issue/1"),
      );
    });
    await screen.findByTestId("tc-surface-mount-controls");

    // Approve directly — no overlay.
    act(() => {
      fireEvent.click(screen.getByTestId("tc-surface-mount-approve"));
    });

    await waitFor(() =>
      expect(decisionRequests(transport, "appr-4")).toHaveLength(1),
    );
    const body = decisionRequests(transport, "appr-4")[0].body as {
      decision: string;
      edits?: unknown;
    };
    expect(body.decision).toBe("approved");
    expect(body.edits).toBeUndefined();
    // The overlay never opened.
    expect(screen.queryByTestId("surface-edit-overlay")).toBeNull();
  });
});

// The shared run-create body builder (used by the shell default + both host
// binders) — one place that maps a RunStartRequest to the POST body.
describe("buildRunCreateBody", () => {
  it("keeps a bare goal to 'conversation + goal only' (byte-unchanged legacy body)", () => {
    expect(buildRunCreateBody(CONV, { goal: "Ship it" })).toEqual({
      conversation_id: CONV,
      user_input: "Ship it",
    });
  });

  it("attaches a non-default model selection (journey: model pill → run body)", () => {
    const model = { provider: "anthropic", model_name: "claude-sonnet-4-5" };
    expect(buildRunCreateBody(CONV, { goal: "Go", model })).toEqual({
      conversation_id: CONV,
      user_input: "Go",
      model,
    });
  });

  it("attaches composer attachments only when present", () => {
    const attachments = [
      { id: "a1", type: "file", name: "airdrop-claims.csv", content: [] },
    ];
    const body = buildRunCreateBody(CONV, { goal: "Explain", attachments });
    expect(body.attachments).toBe(attachments);
    expect(
      buildRunCreateBody(CONV, { goal: "x", attachments: [] }).attachments,
    ).toBeUndefined();
  });

  it("only sends web_search_enabled on an explicit opt-out (true is the runtime default)", () => {
    expect(
      buildRunCreateBody(CONV, { goal: "x", webSearchEnabled: false })
        .web_search_enabled,
    ).toBe(false);
    expect(
      buildRunCreateBody(CONV, { goal: "x", webSearchEnabled: true })
        .web_search_enabled,
    ).toBeUndefined();
    expect(
      buildRunCreateBody(CONV, { goal: "x" }).web_search_enabled,
    ).toBeUndefined();
  });

  it("nests active connector scopes under request_context, omitting an empty map", () => {
    const scopes = { "safe-wallet": [] };
    expect(
      buildRunCreateBody(CONV, { goal: "x", connectorScopes: scopes })
        .request_context,
    ).toEqual({ connector_scopes: scopes });
    expect(
      buildRunCreateBody(CONV, { goal: "x", connectorScopes: {} })
        .request_context,
    ).toBeUndefined();
  });
});
