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
import { afterEach, describe, expect, it } from "vitest";

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
import {
  clearRegistry,
  registerAdapter,
  type SaaSRendererAdapter,
} from "../../surfaces";
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
  eventType: string,
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

function canonicalRowsetEvent(): Record<string, unknown> {
  return v2Event("effect.staged", {
    v: 1,
    stage_id: "stg-rowset",
    operation_id: "op-rowset",
    executor: "builtin",
    target_ref: "builtin-target://linear/update_issue",
    target_digest: "b".repeat(64),
    proposal_ref: "proposal://stg-rowset/revisions/1",
    proposal_content_ref: "operation://op-rowset/args",
    proposal_digest: "a".repeat(64),
    proposal_kind: "row_set",
    proposal_media_type: "application/json",
    policy: "ask",
    display_target: "Renewal changes",
  });
}

function partialRowsetReview(): Record<string, unknown> {
  const rows = [
    {
      row_key: "success",
      title: "Success",
      changes: [{ field: "status", old: "old", new: "new" }],
      decision: "approve",
      decision_source: "user",
      hold_reason: null,
      apply_outcome: "applied",
      can_decide: false,
    },
    {
      row_key: "failed-a",
      title: "Failed A",
      changes: [{ field: "status", old: "old", new: "new" }],
      decision: "approve",
      decision_source: "user",
      hold_reason: null,
      apply_outcome: "failed",
      can_decide: false,
    },
    {
      row_key: "held",
      title: "Held",
      changes: [{ field: "status", old: "old", new: "new" }],
      decision: "hold",
      decision_source: "agent",
      hold_reason: "manual review",
      apply_outcome: null,
      can_decide: false,
    },
    {
      row_key: "failed-b",
      title: "Failed B",
      changes: [{ field: "status", old: "old", new: "new" }],
      decision: "approve",
      decision_source: "user",
      hold_reason: null,
      apply_outcome: "failed",
      can_decide: false,
    },
  ];
  return {
    stage_id: "stg-rowset",
    revision: 1,
    proposal_digest: "a".repeat(64),
    target_digest: "b".repeat(64),
    title: "Renewal changes",
    source_connector: "linear",
    source_op: "update_issue",
    status: "partial",
    rows,
    counts: {
      total: 4,
      approved: 3,
      held: 1,
      applied: 1,
      failed: 2,
    },
    action: {
      kind: "retry_failed",
      row_keys: ["failed-a", "failed-b"],
      basis_sequence_no: 14,
      basis_ledger_id: "rrun·014",
    },
    ledger_id: "rrun·014",
    last_sequence_no: 14,
  };
}

function legacyPartialRowsetEvents(): readonly Record<string, unknown>[] {
  return [
    created("surface-rowset", "table", "Renewal changes"),
    v2Event("write.staged", {
      v: 1,
      stage_id: "stage-rowset",
      surface_id: "surface-rowset",
      target: { connector: "local-csv", op: "update_rows" },
      proposal_ref: "stage://stage-rowset/v1",
      rows: 4,
      agent_holds: [{ row_key: "held", reason: "manual review" }],
    }),
    v2Event("revision.added", {
      v: 1,
      stage_id: "stage-rowset",
      rev: 1,
      author: "agent",
      proposal_ref: "stage://stage-rowset/v1",
      diff_ref: "stage://stage-rowset/v1",
      rowset: {
        rows: [
          {
            row_key: "success",
            title: "Success",
            changes: [{ field: "status", old: "old", new: "new" }],
          },
          {
            row_key: "failed-a",
            title: "Failed A",
            changes: [{ field: "status", old: "old", new: "new" }],
          },
          {
            row_key: "held",
            title: "Held",
            changes: [{ field: "status", old: "old", new: "new" }],
          },
          {
            row_key: "failed-b",
            title: "Failed B",
            changes: [{ field: "status", old: "old", new: "new" }],
          },
        ],
      },
    }),
    v2Event("decision.recorded", {
      v: 1,
      stage_id: "stage-rowset",
      decision: "approve",
      scope: { row_keys: ["success", "failed-a", "failed-b"] },
      actor: "user",
      apply: true,
    }),
    v2Event("write.applied", {
      v: 1,
      stage_id: "stage-rowset",
      rev: 1,
      result: "partial",
      row_results: [
        { row_key: "success", outcome: "applied" },
        { row_key: "failed-a", outcome: "failed" },
        { row_key: "failed-b", outcome: "failed" },
      ],
    }),
  ];
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

/** The ledger's other half. A surface is `tier: "pending"` — and the frame shows
 *  its skeleton instead of the adapter — until a `view.derived` names the tier
 *  the projector settled on, exactly as the live run does
 *  (`view.derived {tier: shaped, basis: schema}`, FINDINGS §4.7). */
function derived(
  surface_id: string,
  tier: "raw" | "generic" | "shaped",
  basis: string,
): Record<string, unknown> {
  return v2Event("view.derived", { v: 1, surface_id, tier, basis });
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

  it("flag ON: a staged write mounts its draft surface and stages nothing on its own", async () => {
    seq = 0;
    const transport = new FakeTransport();
    renderRun(transport, makeStore(), true);
    await screen.findByTestId("thread-canvas");
    // `write.staged` is a LIVE event (`surfaces_v2/staging.py` emits it), and
    // its surface is an ordinary ledger surface, so the D1 draft surface is
    // what belongs on the canvas.
    //
    // This test used to assert the opposite — that the draft surface was
    // SUPPRESSED — because a client compatibility reader classified the whole
    // stream as historic the moment it saw `write.staged` (or a `connector:`
    // subject, or a `call:` payload ref…). Every one of those signals matches
    // current data, so the reader silently disabled a shipped feature on every
    // real run. It is deleted; the assertion follows the code.
    const subject = "message://gmail/create_draft/launch-1";
    stream(transport, [
      created(subject, "message", "Launch draft"),
      v2Event("write.staged", {
        v: 1,
        stage_id: "stage-launch-1",
        surface_id: subject,
        target: { connector: "gmail", op: "send" },
        proposal_ref: "draft://launch-1/v1",
      }),
    ]);
    await screen.findByTestId("tc-staged-draft");
    // Rendering a proposal is not making one: the cockpit posts a decision only
    // when the user presses one of this surface's buttons.
    expect(
      transport.requests.some(
        (request) =>
          request.method === "POST" &&
          (request.path.includes("/stages/") ||
            request.path.includes("/effect-stages/")),
      ),
    ).toBe(false);
  });

  it("keeps an ordinary terminal run out of the Studio canvas and tab strip", async () => {
    seq = 0;
    const transport = new FakeTransport();
    renderRun(transport, makeStore(), true);
    await screen.findByTestId("thread-canvas");

    stream(transport, [
      {
        // The worker emits this canonical lifecycle pair at every terminal
        // state. It is durable audit/export data, not a Studio tab for an
        // ordinary chat/subagent run.
        event_id: "terminal-zero-op-receipt-surface",
        run_id: "run-1",
        conversation_id: "conv-1",
        sequence_no: 1,
        event_type: "surface.created",
        activity_kind: "surface",
        payload: {
          v: 1,
          surface_id: "receipt://run-1",
          kind: "receipt",
          source: { connector: "runtime", op: "receipt" },
          title: "Run receipt",
          payload_ref: "ledger://run-1@0",
        },
        created_at: new Date(1_700_000_001_000).toISOString(),
      },
      {
        event_id: "terminal-zero-op-receipt-emitted",
        run_id: "run-1",
        conversation_id: "conv-1",
        sequence_no: 2,
        event_type: "receipt.emitted",
        activity_kind: "system",
        payload: {
          v: 1,
          surface_id: "receipt://run-1",
          fold_ref: "ledger://run-1@0",
        },
        created_at: new Date(1_700_000_002_000).toISOString(),
      },
      {
        event_id: "terminal-zero-op",
        run_id: "run-1",
        conversation_id: "conv-1",
        sequence_no: 3,
        event_type: "run_completed",
        activity_kind: "run",
        payload: { status: "completed" },
        created_at: new Date(1_700_000_003_000).toISOString(),
      },
    ]);

    await waitFor(() =>
      expect(screen.queryByTestId("receipt-v2-launch")).toBeNull(),
    );
    expect(screen.getByTestId("run-destination")).toHaveAttribute(
      "data-mode",
      "studio",
    );
    // The durable ledger receipt still exists, but ordinary chat/subagent runs
    // do not get a second primary surface in the cockpit.
    expect(surfaceTabs()).toHaveLength(0);
    expect(screen.queryByTestId("receipt-v2-surface")).toBeNull();
  });

  it("offers a compact receipt review only for consequential work", async () => {
    seq = 0;
    const transport = new FakeTransport();
    renderRun(transport, makeStore(), true);
    await screen.findByTestId("thread-canvas");

    stream(transport, [
      {
        event_id: "effect-staged",
        run_id: "run-1",
        conversation_id: "conv-1",
        sequence_no: 1,
        event_type: "effect.staged",
        activity_kind: "tool",
        payload: {
          v: 1,
          stage_id: "stg_018f47a6-7b2c-7c10-8f21-12345678c005",
          operation_id: "op_018f47a6-7b2c-7a10-8f21-12345678a005",
          executor: "workspace",
          target_ref: "workspace-target://grant_05/pathToken_05",
          target_digest:
            "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
          proposal_ref:
            "proposal://stg_018f47a6-7b2c-7c10-8f21-12345678c005/revisions/1",
          proposal_digest:
            "85fa12d00a9e3d3fee0f86fa9d03757562e5d0e0b758c16cae83cdf72794ebbc",
          policy: "ask",
        },
        created_at: new Date(1_700_000_001_000).toISOString(),
      },
      {
        event_id: "terminal-consequential",
        run_id: "run-1",
        conversation_id: "conv-1",
        sequence_no: 2,
        event_type: "run_completed",
        activity_kind: "run",
        payload: { status: "completed" },
        created_at: new Date(1_700_000_002_000).toISOString(),
      },
    ]);

    const launcher = await screen.findByTestId("receipt-v2-launch");
    expect(launcher).toHaveTextContent("Run receipt");
    expect(launcher).toHaveTextContent("Review");
    expect(launcher).not.toHaveTextContent("This receipt was assembled");
    fireEvent.click(screen.getByTestId("receipt-v2-open"));
    await screen.findByTestId("receipt-v2-surface");
    expect(screen.queryByTestId("receipt-v2-launch")).toBeNull();
  });

  it("keeps the consequential receipt out of Focus and opens it only from Studio", async () => {
    seq = 0;
    const transport = new FakeTransport();
    renderRun(transport, makeStore(), true);
    await screen.findByTestId("thread-canvas");

    stream(transport, [
      {
        event_id: "effect-staged-focus",
        run_id: "run-1",
        conversation_id: "conv-1",
        sequence_no: 1,
        event_type: "effect.staged",
        activity_kind: "tool",
        payload: {
          v: 1,
          stage_id: "stg_018f47a6-7b2c-7c10-8f21-12345678c006",
          operation_id: "op_018f47a6-7b2c-7a10-8f21-12345678a006",
          executor: "workspace",
          target_ref: "workspace-target://grant_06/pathToken_06",
          target_digest:
            "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
          proposal_ref:
            "proposal://stg_018f47a6-7b2c-7c10-8f21-12345678c006/revisions/1",
          proposal_digest:
            "85fa12d00a9e3d3fee0f86fa9d03757562e5d0e0b758c16cae83cdf72794ebbc",
          policy: "ask",
        },
        created_at: new Date(1_700_000_002_000).toISOString(),
      },
      {
        event_id: "terminal-focus-receipt",
        run_id: "run-1",
        conversation_id: "conv-1",
        sequence_no: 2,
        event_type: "run_completed",
        activity_kind: "run",
        payload: { status: "completed" },
        created_at: new Date(1_700_000_003_000).toISOString(),
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

  it("submits every and only failed row from the projected recovery context", async () => {
    seq = 0;
    const transport = new FakeTransport();
    transport.requestHandler = async (request) => {
      if (request.path.includes("/rowset/review")) return partialRowsetReview();
      if (request.path.includes("/rowset/retry")) return partialRowsetReview();
      return request.path.includes("/messages")
        ? { messages: [] }
        : {
            latest_run_id: "run-1",
            latest_run_id_any_status: "run-1",
            runs: [],
          };
    };
    renderRun(transport, makeStore(), true);
    await screen.findByTestId("thread-canvas");
    stream(transport, [canonicalRowsetEvent()]);

    const retry = await screen.findByTestId("tc-bulk-retry");
    expect(retry).toHaveTextContent("Retry 2 failed");
    fireEvent.click(retry);

    await waitFor(() => {
      const request = transport.requests.find(
        (item) =>
          item.method === "POST" &&
          item.path.includes("/effect-stages/stg-rowset/rowset/retry"),
      );
      expect(request?.body).toEqual({
        revision: 1,
        proposal_digest: "a".repeat(64),
        target_digest: "b".repeat(64),
        row_keys: ["failed-a", "failed-b"],
        basis_sequence_no: 14,
        basis_ledger_id: "rrun·014",
      });
    });
    expect(
      (screen.getByTestId("tc-bulk-retry") as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(screen.getByTestId("tc-bulk-retry")).toHaveTextContent("Retrying…");
  });

  it("fails a rejected retry locally without widening or hiding recovery", async () => {
    seq = 0;
    const transport = new FakeTransport();
    transport.requestHandler = async (request) => {
      if (
        request.method === "POST" &&
        request.path.includes("/effect-stages/stg-rowset/rowset/retry")
      ) {
        throw new Error("rejected");
      }
      if (request.path.includes("/rowset/review")) return partialRowsetReview();
      return request.path.includes("/messages")
        ? { messages: [] }
        : {
            latest_run_id: "run-1",
            latest_run_id_any_status: "run-1",
            runs: [],
          };
    };
    renderRun(transport, makeStore(), true);
    await screen.findByTestId("thread-canvas");
    stream(transport, [canonicalRowsetEvent()]);

    fireEvent.click(await screen.findByTestId("tc-bulk-retry"));

    await waitFor(() =>
      expect(screen.getByTestId("tc-bulk-pledge")).toHaveTextContent(
        "Successful and held rows were not touched.",
      ),
    );
    expect(
      (screen.getByTestId("tc-bulk-retry") as HTMLButtonElement).disabled,
    ).toBe(false);
    expect(screen.getByTestId("tc-bulk-retry")).toHaveTextContent(
      "Retry 2 failed",
    );
  });

  it("keeps historical write.staged rowsets read-only after cutover", async () => {
    seq = 0;
    const transport = new FakeTransport();
    renderRun(transport, makeStore(), true);
    await screen.findByTestId("thread-canvas");
    stream(transport, legacyPartialRowsetEvents());

    await waitFor(() =>
      expect(screen.queryByTestId("tc-bulk-retry")).toBeNull(),
    );
    expect(
      transport.requests.some((request) =>
        request.path.includes("/effect-stages/"),
      ),
    ).toBe(false);
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
    const slot = screen.getByTestId("run-canvas-slot");
    expect(slot.getAttribute("data-visible")).toBe("true");

    // Switch to Focus: the surface column is hidden → no generative surfaces.
    fireEvent.click(screen.getByTestId("run-mode-focus"));
    await waitFor(() =>
      expect(
        screen.getByTestId("run-canvas-slot").getAttribute("data-visible"),
      ).toBe("false"),
    );

    // Back to Studio: canvas re-shown.
    fireEvent.click(screen.getByTestId("run-mode-studio"));
    await waitFor(() =>
      expect(
        screen.getByTestId("run-canvas-slot").getAttribute("data-visible"),
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

// ---------------------------------------------------------------------------
// Mount/tab identity — the whole join, driven end to end
// ---------------------------------------------------------------------------
//
// The tests above stream synthetic short ids (`s_issue`) and stop at the tab
// strip — which is exactly why nothing here caught the delivery break the live
// journey found (FINDINGS §4.7). This block drives the whole join instead:
// `surface.created` → `view.derived` → tab URI → `GET /surfaces` → the
// adapter's `renderCurrent`, on a REAL projector-minted id
// (`SurfaceProjector._build_uri`).
//
// It asserts one thing the previous shape could not offer: that the SAME string
// travels every hop. The tab URI used to be a second identity — the surface id
// wrapped as `<scheme>://surfaces-v2/<id>`, whose outer scheme came from the
// ledger's lossy `kind` — so a `doc://` surface mounted the RECORD adapter, and
// every state lookup had to decode before it could join. A decode that missed
// did not error; it handed the renderer `undefined`, indistinguishable from a
// surface with no data. There is no wrapper now, and this test fails the moment
// one comes back.

/** The id shape the projector mints: `<archetype>://<connector>/<tool>/<id>`. */
const PROJECTOR_ID = "table://incidents/list_incidents/1532a206699e";

describe("RunDestination — v2 mount identity (one id, no round-trip)", () => {
  afterEach(() => {
    // The rest of this file runs against an EMPTY registry (its assertions are
    // about the tier-3 / placeholder floor), so an adapter registered here must
    // not outlive the test that needed it.
    clearRegistry();
  });

  /** Records every state the mount hands the adapter, and renders it. */
  function recordingTableAdapter(seen: unknown[]): SaaSRendererAdapter {
    return {
      scheme: "table",
      matches: (uri: string) => uri.startsWith("table://"),
      renderCurrent: (state: unknown) => {
        seen.push(state);
        return <div data-testid="table-adapter">{JSON.stringify(state)}</div>;
      },
      renderDiff: () => <div data-testid="table-adapter-diff" />,
      metadata: { origin: "first-party", schemaVersion: 1 },
    };
  }

  function hydrating(transport: FakeTransport, surfaceId: string): void {
    transport.requestHandler = async (req) => {
      if (req.path.endsWith("/surfaces")) {
        return {
          surfaces: [
            {
              surface_id: surfaceId,
              state: {
                spec: { archetype: "table" },
                data: { incidents: [{ id: "INC-4127" }] },
              },
            },
          ],
        };
      }
      return req.path.includes("/messages")
        ? { messages: [] }
        : {
            latest_run_id: "run-1",
            latest_run_id_any_status: "run-1",
            runs: [],
          };
    };
  }

  it("delivers hydrated content to the adapter the surface id's scheme names", async () => {
    seq = 0;
    const seen: unknown[] = [];
    registerAdapter(recordingTableAdapter(seen));
    const transport = new FakeTransport();
    hydrating(transport, PROJECTOR_ID);
    renderRun(transport, makeStore(), true);
    await screen.findByTestId("thread-canvas");
    stream(transport, [
      created(PROJECTOR_ID, "table", "Open incidents"),
      derived(PROJECTOR_ID, "shaped", "schema"),
    ]);

    // The `table://` scheme on the id itself resolved the table adapter — no
    // scheme was re-derived from the ledger's lossier `kind`.
    const rendered = await screen.findByTestId("table-adapter");
    expect(screen.getByTestId("tc-surface-mount").dataset.tier).toBe("primary");
    // …and the payload arrived. This is the assertion that was false in
    // production while ~9,000 unit tests passed.
    expect(rendered).toHaveTextContent("INC-4127");
    expect(seen.at(-1)).toEqual({
      spec: { archetype: "table" },
      data: { incidents: [{ id: "INC-4127" }] },
    });
  });

  it("shows the tab and the card one identity colour, from the one URI", async () => {
    seq = 0;
    registerAdapter(recordingTableAdapter([]));
    const transport = new FakeTransport();
    hydrating(transport, PROJECTOR_ID);
    renderRun(transport, makeStore(), true);
    await screen.findByTestId("thread-canvas");
    stream(transport, [
      created(PROJECTOR_ID, "table", "Open incidents"),
      derived(PROJECTOR_ID, "shaped", "schema"),
    ]);

    // `surfaceHueForUri` reads the same string the strip mounts, so the dot on
    // the tab and the hue scope on the card cannot drift. `table` ⇒ jade.
    await screen.findByTestId("table-adapter");
    const tab = within(screen.getByTestId("tc-tabs")).getByRole("tab");
    expect(tab.dataset.uri).toBe(PROJECTOR_ID);
    expect(tab.dataset.surfaceHue).toBe("jade");
    expect(screen.getByTestId("tc-surface-mount").dataset.surfaceHue).toBe(
      "jade",
    );
  });

  it("keeps ONE identity per surface — an old envelope mints no second tab", async () => {
    seq = 0;
    const seen: unknown[] = [];
    registerAdapter(recordingTableAdapter(seen));
    const transport = new FakeTransport();
    hydrating(transport, PROJECTOR_ID);
    renderRun(transport, makeStore(), true);
    await screen.findByTestId("thread-canvas");
    // The stream carries a modern surface AND a pre-ledger `tool_result`
    // presentation envelope. A client compatibility reader used to read the
    // pair as proof the whole run was historic — and then mint
    // `<scheme>://legacy-v2/<percent-encoded surface_id>` for the MODERN
    // surface too, and resolve that tab's content from a map only the reader
    // ever filled. Live trace: `stored keys: ["table://incidents/…"]` beside
    // `resolve uri: "table://legacy-v2/table%3A%2F%2F…" resolved: false` — a
    // fully hydrated table rendering as an empty card.
    //
    // The lane is deleted. The old envelope contributes no tab at all, and the
    // ledger's surface is mounted under the one id its producer minted.
    stream(transport, [
      created(PROJECTOR_ID, "table", "Open incidents"),
      derived(PROJECTOR_ID, "shaped", "schema"),
      v2Event("tool_result", {
        surface: {
          surface_uri: "sheet-row://legacy/x",
          archetype: "table",
          state: { data: {} },
        },
      }),
    ]);

    const rendered = await screen.findByTestId("table-adapter");
    expect(rendered).toHaveTextContent("INC-4127");
    const tabs = within(screen.getByTestId("tc-tabs")).getAllByRole("tab");
    expect(tabs.map((tab) => tab.dataset.uri)).toEqual([PROJECTOR_ID]);
  });
});
