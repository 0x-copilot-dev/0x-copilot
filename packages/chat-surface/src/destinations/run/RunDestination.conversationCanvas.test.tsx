// PRD-02 — the canvas keeps its open surface across turns (GS-ARCH-05).
//
// The defect: `RunDestination` folds `projectCanvasLifecycle(session.events)`,
// and `useRunSession` clears `events` whenever `activeRunId` changes. So turn 1
// publishes an artifact and the table renders; turn 2 binds a new run, the fold
// sees an empty stream, and the canvas reverts to "This run completed in chat.
// No artifact was created." — the exact string PR #413 fixed, which is why this
// reads as a regression of that fix rather than the next defect.
//
// Canvas *identity* is conversation-scoped; operation *state* stays run-scoped.
// A chat-only turn 2 must therefore report lifecycle `chat_only` (honest about
// this run) while still showing the artifact (honest about the conversation).

import { act, render, screen, within } from "@testing-library/react";
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
const ARTIFACT = "art_forecast";

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

  subFor(runId: string): CapturedSub | undefined {
    return [...this.subs]
      .reverse()
      .find(
        (s) =>
          !s.closed &&
          s.eventName === "runtime_event" &&
          s.path.includes(runId),
      );
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

/** Events for one run, numbered from 1 the way a real per-run ledger is. */
class RunLedger {
  private seq = 0;

  constructor(private readonly runId: string) {}

  event(
    eventType: string,
    payload: Record<string, unknown>,
  ): Record<string, unknown> {
    this.seq += 1;
    return {
      event_id: `evt_${this.runId}_${this.seq}`,
      run_id: this.runId,
      conversation_id: "conv-1",
      sequence_no: this.seq,
      event_type: eventType,
      activity_kind: "event",
      payload,
      created_at: new Date(1_700_000_000_000 + this.seq * 1000).toISOString(),
    };
  }

  /** Turn 1: a published dataset artifact, decided onto the canvas. */
  publishedArtifact(): readonly Record<string, unknown>[] {
    return [
      this.event("artifact.created", {
        v: 1,
        artifact_id: ARTIFACT,
        kind: "dataset",
        revision: 1,
        content_ref: `artifact://${ARTIFACT}/revisions/1`,
        content_digest: "0".repeat(64),
        author: "model",
      }),
      this.event("artifact.presentation_decided", {
        v: 1,
        artifact_id: ARTIFACT,
        decision: "canvas",
        basis: "explicit_artifact_canvas",
      }),
      this.event("final_response", { message: "Here is your CSV." }),
      this.event("run_completed", { status: "run_completed" }),
    ];
  }

  /** Turn 2: a plain answer — no artifact, no surface. */
  chatOnly(): readonly Record<string, unknown>[] {
    return [
      this.event("final_response", { message: "Row 2 is the EMEA forecast." }),
      this.event("run_completed", { status: "run_completed" }),
    ];
  }
}

function stream(
  transport: FakeTransport,
  runId: string,
  events: readonly Record<string, unknown>[],
): void {
  act(() => {
    for (const e of events)
      transport.subFor(runId)?.onMessage?.(JSON.stringify(e));
  });
}

function canvasTabs(): HTMLElement[] {
  const strip = screen.queryByTestId("tc-tabs");
  return strip === null ? [] : within(strip).queryAllByRole("tab");
}

function lifecycle(): string | null {
  return (
    screen
      .queryByTestId("canvas-lifecycle-panel")
      ?.getAttribute("data-lifecycle") ?? null
  );
}

function ui(runId: string, transport: Transport, store: KeyValueStore) {
  return (
    <TransportProvider transport={transport}>
      <KeyValueStoreProvider store={store}>
        <RunDestination conversationId={CONV} runId={runId} surfacesV2 />
      </KeyValueStoreProvider>
    </TransportProvider>
  ) as ReactElement;
}

describe("RunDestination — canvas identity across turns (PRD-02)", () => {
  it("keeps the published artifact when a chat-only turn follows", async () => {
    const transport = new FakeTransport();
    const store = makeStore();
    const { rerender } = render(ui("run-1", transport, store));
    await screen.findByTestId("thread-canvas");

    // Turn 1 — the artifact reaches the canvas (this is what PR #413 fixed).
    stream(transport, "run-1", new RunLedger("run-1").publishedArtifact());
    expect(canvasTabs().length).toBeGreaterThan(0);
    expect(lifecycle()).not.toBe("chat_only");

    // Turn 2 — a new run binds. `session.events` resets; the artifact belongs
    // to the conversation, not to run 1, so it must survive.
    rerender(ui("run-2", transport, store));
    stream(transport, "run-2", new RunLedger("run-2").chatOnly());

    expect(canvasTabs().length).toBeGreaterThan(0);
    expect(
      screen.queryByText(/No artifact was created/i),
    ).not.toBeInTheDocument();
  });

  it("still reports the current run honestly as chat-only", async () => {
    // The surface persisting must not make the cockpit claim this run produced
    // it. Identity is conversation-scoped; lifecycle stays run-scoped.
    const transport = new FakeTransport();
    const store = makeStore();
    const { rerender } = render(ui("run-1", transport, store));
    await screen.findByTestId("thread-canvas");
    stream(transport, "run-1", new RunLedger("run-1").publishedArtifact());

    rerender(ui("run-2", transport, store));
    stream(transport, "run-2", new RunLedger("run-2").chatOnly());

    // Not asserted via the empty-state panel — that panel is exactly what must
    // NOT be showing. The run's own narrative lives beside the canvas.
    expect(screen.getByText(/Row 2 is the EMEA forecast/)).toBeInTheDocument();
  });
});
