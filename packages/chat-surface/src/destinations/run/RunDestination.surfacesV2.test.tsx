// RunDestination — Generative Surfaces v2 flag branch (PRD-B1).
//
// A SEPARATE file (the pre-existing RunDestination.test.tsx stays untouched —
// its green run is the flag-off byte-identity proof). Here we assert the flag
// ON behavior: v2 tabs from the ledger fold, activation, strictness (no v1
// leak), hostile-title safety, and the Studio-shell DoD clauses FR-A7 / FR-F1.

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
  closed: boolean;
}

class FakeTransport implements Transport {
  requestHandler: (req: TypedRequest) => Promise<unknown> = async (req) =>
    req.path.includes("/messages")
      ? { messages: [] }
      : { latest_run_id: "run-1", latest_run_id_any_status: "run-1", runs: [] };
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
      closed: false,
    };
    this.subs.push(sub);
    return { close: () => (sub.closed = true) };
  }

  getSession(): Session {
    return { bearer: null };
  }
  capabilities(): TransportCapabilities {
    return CAPABILITIES;
  }

  get sessionSub(): CapturedSub | undefined {
    return [...this.subs]
      .reverse()
      .find((s) => !s.closed && s.eventName === "runtime_event");
  }

  get surfacesRequests(): TypedRequest[] {
    return this.requests.filter((r) => r.path.endsWith("/surfaces"));
  }
}

function makeStore(): KeyValueStore {
  const map = new Map<string, string>();
  return {
    get: (k) => map.get(k) ?? null,
    set: (k, v) => {
      if (v === null) map.delete(k);
      else map.set(k, v);
    },
    keys: (prefix) =>
      [...map.keys()].filter(
        (k) => prefix === undefined || k.startsWith(prefix),
      ),
  };
}

let seq = 0;
function v2Event(
  eventType: "surface.created" | "view.derived",
  payload: Record<string, unknown>,
): Record<string, unknown> {
  seq += 1;
  return {
    event_id: `evt_${seq}`,
    run_id: "run-1",
    conversation_id: "conv-1",
    sequence_no: seq,
    event_type: eventType,
    activity_kind: "tool",
    payload,
    created_at: new Date(1_700_000_000_000 + seq * 1000).toISOString(),
  };
}

function created(
  surface_id: string,
  kind: string,
  title: string,
): Record<string, unknown> {
  return v2Event("surface.created", {
    v: 1,
    surface_id,
    kind,
    source: { connector: "linear", op: "get_issue" },
    title,
    payload_ref: `payload/${surface_id}`,
  });
}

function stream(
  transport: FakeTransport,
  events: readonly Record<string, unknown>[],
): void {
  act(() => {
    for (const e of events)
      transport.sessionSub?.onMessage?.(JSON.stringify(e));
  });
}

/** Surface tabs live in the `tc-tabs` strip; the workspace rail ALSO uses
 *  `role="tab"`, so every surface-tab query is scoped to the strip. */
function surfaceTabs(): HTMLElement[] {
  const strip = screen.queryByTestId("tc-tabs");
  return strip === null ? [] : within(strip).queryAllByRole("tab");
}

function renderRun(
  transport: Transport,
  store: KeyValueStore,
  surfacesV2: boolean,
): void {
  const ui: ReactElement = (
    <TransportProvider transport={transport}>
      <KeyValueStoreProvider store={store}>
        <RunDestination conversationId={CONV} surfacesV2={surfacesV2} />
      </KeyValueStoreProvider>
    </TransportProvider>
  );
  render(ui);
}

describe("RunDestination — Generative Surfaces v2 flag (PRD-B1)", () => {
  it("flag OFF: v2 events produce no tabs and zero /surfaces requests", async () => {
    seq = 0;
    const transport = new FakeTransport();
    renderRun(transport, makeStore(), false);
    await screen.findByTestId("thread-canvas");
    stream(transport, [created("s_issue", "record", "ENG-142")]);

    // v1 selector ignores v2 event types → no surface tabs; and never hydrates.
    expect(surfaceTabs()).toHaveLength(0);
    expect(transport.surfacesRequests).toHaveLength(0);
  });

  it("flag ON: seeded v2 events render named tabs, newest first", async () => {
    seq = 0;
    const transport = new FakeTransport();
    renderRun(transport, makeStore(), true);
    await screen.findByTestId("thread-canvas");
    stream(transport, [
      created("s_issue", "record", "ENG-142 Fix reconnect"),
      created("s_list", "table", "Sprint backlog"),
    ]);

    await waitFor(() => {
      // Tab textContent is `<title>×` (title span + close button) — strip the ×.
      expect(
        surfaceTabs().map((t) => t.textContent?.replace(/×$/, "")),
      ).toEqual(["Sprint backlog", "ENG-142 Fix reconnect"]);
    });
  });

  it("flag ON: activating a tab switches the active surface", async () => {
    seq = 0;
    const transport = new FakeTransport();
    renderRun(transport, makeStore(), true);
    await screen.findByTestId("thread-canvas");
    stream(transport, [
      created("s_issue", "record", "ENG-142"),
      created("s_list", "table", "Backlog"),
    ]);

    const strip = await screen.findByTestId("tc-tabs");
    const issueTab = within(strip).getByText("ENG-142");
    // Newest ("Backlog") is active by default; click the issue tab to pin it.
    fireEvent.click(issueTab);
    await waitFor(() => {
      const tab = issueTab.closest('[role="tab"]');
      expect(tab?.getAttribute("aria-selected")).toBe("true");
    });
  });

  it("flag ON: a historic envelope replays through read-only renderer state", async () => {
    seq = 0;
    const transport = new FakeTransport();
    renderRun(transport, makeStore(), true);
    await screen.findByTestId("thread-canvas");
    // The compatibility reader is selective: only an explicit historic surface
    // envelope becomes fixed-renderer state, preserving its old URI/state
    // rather than upgrading it into a v2 stage or writable surface. The
    // production default is Studio, so the tab strip remains available.
    stream(transport, [
      {
        event_id: "v1-1",
        run_id: "run-1",
        conversation_id: "conv-1",
        sequence_no: 1,
        event_type: "tool_result",
        activity_kind: "tool",
        payload: {
          surface: {
            surface_uri: "sheet-row://legacy/x",
            archetype: "table",
            state: { data: {} },
          },
        },
        created_at: new Date().toISOString(),
      },
    ]);
    await waitFor(() => {
      expect(screen.getByTestId("surface-placeholder")).toHaveTextContent(
        "sheet-row",
      );
    });
    expect(screen.queryByTestId("tc-surface-mount-controls")).toBeNull();
    expect(
      transport.requests.some(
        (request) =>
          request.method === "POST" && request.path.includes("/stages/"),
      ),
    ).toBe(false);
  });

  it("flag ON: a historic staged write never becomes an actionable effect", async () => {
    seq = 0;
    const transport = new FakeTransport();
    renderRun(transport, makeStore(), true);
    await screen.findByTestId("thread-canvas");
    const subject = "connector:gmail:draft:legacy-1";
    stream(transport, [
      created(subject, "message", "Historic launch draft"),
      {
        event_id: "legacy-stage-1",
        run_id: "run-1",
        conversation_id: "conv-1",
        sequence_no: 2,
        event_type: "write.staged",
        activity_kind: "tool",
        payload: {
          v: 1,
          stage_id: "legacy-stage-1",
          surface_id: subject,
          target: { connector: "gmail", op: "send" },
          proposal_ref: "draft://legacy-1/v1",
        },
        created_at: new Date().toISOString(),
      },
    ]);
    await waitFor(() => {
      expect(screen.getByTestId("tc-surface-mount").dataset.tier).not.toBe(
        "empty",
      );
    });
    expect(screen.queryByTestId("effect-stage-card")).toBeNull();
    expect(screen.queryByTestId("tc-surface-mount-controls")).toBeNull();
    expect(
      transport.requests.some(
        (request) =>
          request.method === "POST" &&
          (request.path.includes("/stages/") ||
            request.path.includes("/effect-stages/")),
      ),
    ).toBe(false);
  });

  it("E1 D4: a terminal zero-op run presents its receipt in Studio and opens it only on request", async () => {
    seq = 0;
    const transport = new FakeTransport();
    renderRun(transport, makeStore(), true);
    await screen.findByTestId("thread-canvas");

    stream(transport, [
      {
        event_id: "terminal-zero-op",
        run_id: "run-1",
        conversation_id: "conv-1",
        sequence_no: 1,
        event_type: "run_completed",
        activity_kind: "run",
        payload: { status: "completed" },
        created_at: new Date(1_700_000_001_000).toISOString(),
      },
    ]);

    await screen.findByTestId("receipt-v2-launch");
    expect(screen.getByTestId("run-destination")).toHaveAttribute(
      "data-mode",
      "studio",
    );
    // The zero-op receipt stays out of the lifecycle tabs until explicitly opened.
    expect(surfaceTabs()).toHaveLength(0);
    expect(screen.queryByTestId("receipt-v2-surface")).toBeNull();

    fireEvent.click(screen.getByTestId("receipt-v2-open"));
    await screen.findByTestId("receipt-v2-surface");
    expect(screen.queryByTestId("receipt-v2-launch")).toBeNull();
  });

  it("E1 D4: a terminal receipt is absent from Focus and opens only in Studio", async () => {
    seq = 0;
    const transport = new FakeTransport();
    renderRun(transport, makeStore(), true);
    await screen.findByTestId("thread-canvas");

    stream(transport, [
      {
        event_id: "terminal-focus-receipt",
        run_id: "run-1",
        conversation_id: "conv-1",
        sequence_no: 1,
        event_type: "run_completed",
        activity_kind: "run",
        payload: { status: "completed" },
        created_at: new Date(1_700_000_002_000).toISOString(),
      },
    ]);

    await screen.findByTestId("receipt-v2-launch");
    fireEvent.click(screen.getByTestId("run-mode-focus"));
    await waitFor(() =>
      expect(screen.getByTestId("run-destination")).toHaveAttribute(
        "data-mode",
        "focus",
      ),
    );
    // The active design makes Focus a chat/activity view. Neither the full
    // v2 receipt nor the Studio launcher may leak into its review-card stack.
    expect(screen.queryByTestId("receipt-v2-surface")).toBeNull();
    expect(screen.queryByTestId("receipt-v2-launch")).toBeNull();

    fireEvent.click(screen.getByTestId("run-mode-studio"));
    await waitFor(() =>
      expect(screen.getByTestId("run-destination")).toHaveAttribute(
        "data-mode",
        "studio",
      ),
    );
    fireEvent.click(screen.getByTestId("receipt-v2-open"));
    await screen.findByTestId("receipt-v2-surface");
  });

  it("flag ON: a hostile title renders as text, not markup (no injection)", async () => {
    seq = 0;
    const transport = new FakeTransport();
    renderRun(transport, makeStore(), true);
    await screen.findByTestId("thread-canvas");
    const hostile = '<img src=x onerror="alert(1)">';
    stream(transport, [created("s_x", "record", hostile)]);
    const tab = await screen.findByText(hostile);
    // The string is rendered as text content; no <img> element was created.
    expect(tab.querySelector("img")).toBeNull();
    expect(tab.textContent).toBe(hostile);
  });

  // --- Studio shell & posture DoD -----------------------------------------

  it("FR-F1: Studio mounts the canvas; Focus hides it (mode → visibility gate)", async () => {
    seq = 0;
    const transport = new FakeTransport();
    const store = makeStore();
    renderRun(transport, store, true);
    await screen.findByTestId("thread-canvas");
    stream(transport, [created("s_issue", "record", "ENG-142")]);

    // Studio (default): the surface column is visible → canvas mounted.
    const slot = screen.getByTestId("tc-surface-slot");
    expect(slot.getAttribute("data-visible")).toBe("true");

    // Switch to Focus: the surface column is hidden → no generative surfaces.
    fireEvent.click(screen.getByTestId("run-mode-focus"));
    await waitFor(() =>
      expect(
        screen.getByTestId("tc-surface-slot").getAttribute("data-visible"),
      ).toBe("false"),
    );

    // Back to Studio: canvas re-shown.
    fireEvent.click(screen.getByTestId("run-mode-studio"));
    await waitFor(() =>
      expect(
        screen.getByTestId("tc-surface-slot").getAttribute("data-visible"),
      ).toBe("true"),
    );
  });

  it("FR-A7: a v2 gate surface renders no approval/decision control in the chat rail", async () => {
    seq = 0;
    const transport = new FakeTransport();
    renderRun(transport, makeStore(), true);
    await screen.findByTestId("thread-canvas");
    // A gate surface tabs on the canvas (tier-3); it must NEVER produce a
    // chat-rail Approve/Reject control (v2 decisions live on the canvas — the
    // v1 approval affordance derives from v1 projections and can't match a v2
    // URI, so it is inert by construction).
    stream(transport, [created("s_gate", "gate", "Connect Linear")]);

    await screen.findByText("Connect Linear"); // the gate tab is on the canvas
    expect(screen.queryByRole("button", { name: /approve/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /reject/i })).toBeNull();
  });
});
