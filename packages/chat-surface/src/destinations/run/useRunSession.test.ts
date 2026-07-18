// useRunSession — host-hook unit tests (PR-3.3).
//
// Everything the hook touches is a Transport-port call, so the whole surface
// is exercised through a purpose-built in-memory fake: `FakeTransport`
// implements `Transport`, resolves the run list from a swappable handler, and
// captures every SSE subscription so a test can drive `onMessage`/`onError`
// by hand and assert the resume cursor (`?after_sequence=N`) on re-subscribe.

import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import { TransportProvider } from "../../providers/TransportProvider";
import { useRunSession, type UseRunSessionOptions } from "./useRunSession";

// --- fake transport --------------------------------------------------------

interface CapturedSub {
  readonly path: string;
  readonly query?: SseSubscribeOptions["query"];
  readonly eventName?: string;
  readonly onMessage: (raw: string) => void;
  readonly onError?: (err: Error) => void;
  closed: boolean;
}

const CAPABILITIES: TransportCapabilities = {
  substrate: "web",
  nativeSecretStorage: false,
  fileSystemAccess: false,
  clipboardWrite: false,
  openExternal: false,
};

class FakeTransport implements Transport {
  requestHandler: (req: TypedRequest) => Promise<unknown> = async () => ({
    runs: [],
  });
  readonly requests: TypedRequest[] = [];
  readonly subs: CapturedSub[] = [];

  async request<TRes>(req: TypedRequest): Promise<TRes> {
    this.requests.push(req);
    return (await this.requestHandler(req)) as TRes;
  }

  subscribeServerSentEvents(opts: SseSubscribeOptions): SseSubscription {
    const sub: CapturedSub = {
      path: opts.path,
      query: opts.query,
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

  getSession(): Session {
    return { bearer: null };
  }

  capabilities(): TransportCapabilities {
    return CAPABILITIES;
  }

  /** The most recently opened, still-open subscription. */
  get activeSub(): CapturedSub | undefined {
    return [...this.subs].reverse().find((sub) => !sub.closed);
  }

  emit(sub: CapturedSub | undefined, envelope: RuntimeEventEnvelope): void {
    if (sub === undefined) {
      throw new Error("emit: no subscription");
    }
    act(() => {
      sub.onMessage(JSON.stringify(envelope));
    });
  }

  fail(sub: CapturedSub | undefined, err: Error): void {
    if (sub === undefined) {
      throw new Error("fail: no subscription");
    }
    act(() => {
      sub.onError?.(err);
    });
  }
}

function makeEvent(
  overrides: Partial<RuntimeEventEnvelope> & { sequence_no: number },
): RuntimeEventEnvelope {
  return {
    event_id: `evt-${overrides.sequence_no}`,
    run_id: "run-1",
    conversation_id: "conv-1",
    event_type: "progress",
    activity_kind: "event",
    payload: {},
    created_at: new Date(
      1_700_000_000_000 + overrides.sequence_no,
    ).toISOString(),
    ...overrides,
  };
}

function renderRunSession(
  transport: FakeTransport,
  options: UseRunSessionOptions,
) {
  return renderHook((props: UseRunSessionOptions) => useRunSession(props), {
    initialProps: options,
    wrapper: ({ children }: { children: ReactNode }) =>
      createElement(TransportProvider, { transport, children }),
  });
}

// --- tests -----------------------------------------------------------------

describe("useRunSession — run resolution", () => {
  it("resolves the run list via GET /v1/agent/runs scoped to the conversation", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async () => ({
      runs: [{ run_id: "run-1", status: "running", goal: "Ship it" }],
    });

    const { result } = renderRunSession(transport, {
      conversationId: "conv-1",
    });

    await waitFor(() => expect(result.current.runs).toHaveLength(1));
    const listRequest = transport.requests.find(
      (req) => req.path === "/v1/agent/runs",
    );
    expect(listRequest?.method).toBe("GET");
    expect(listRequest?.query).toEqual({ conversation_id: "conv-1" });
    expect(result.current.runs[0]).toEqual({
      runId: "run-1",
      status: "running",
      goal: "Ship it",
      startedAt: null,
    });
    expect(result.current.runId).toBe("run-1");
  });

  it("auto-selects the live (non-terminal) run when several exist", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async () => ({
      runs: [
        {
          run_id: "run-done",
          status: "completed",
          started_at: "2026-01-01T00:00:00Z",
        },
        {
          run_id: "run-live",
          status: "running",
          started_at: "2026-01-02T00:00:00Z",
        },
      ],
    });

    const { result } = renderRunSession(transport, {
      conversationId: "conv-1",
    });

    await waitFor(() => expect(result.current.runs).toHaveLength(2));
    expect(result.current.runId).toBe("run-live");
  });

  it("reports idle with no run when the conversation has no runs", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async () => ({ runs: [] });

    const { result } = renderRunSession(transport, {
      conversationId: "conv-1",
    });

    await waitFor(() => expect(result.current.status).toBe("idle"));
    expect(result.current.runId).toBeNull();
    expect(transport.subs).toHaveLength(0);
  });

  it("streams an explicit runId even before the list resolves (empty→live)", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async () => ({ runs: [] });

    const { result } = renderRunSession(transport, {
      conversationId: "conv-1",
      runId: "run-new",
    });

    expect(result.current.runId).toBe("run-new");
    await waitFor(() => expect(transport.activeSub).toBeDefined());
    expect(transport.activeSub?.path).toBe("/v1/agent/runs/run-new/stream");
  });
});

describe("useRunSession — lifecycle status", () => {
  it("moves resolving → connecting → streaming as it binds and receives events", async () => {
    const transport = new FakeTransport();
    let resolveRuns: (value: unknown) => void = () => {};
    transport.requestHandler = () =>
      new Promise((resolve) => {
        resolveRuns = resolve;
      });

    const { result } = renderRunSession(transport, {
      conversationId: "conv-1",
    });

    // Request in flight, no run yet → resolving.
    expect(result.current.status).toBe("resolving");

    await act(async () => {
      resolveRuns({ runs: [{ run_id: "run-1", status: "running" }] });
    });

    // Subscribed, no event projected yet → connecting (FR-3.33).
    await waitFor(() => expect(result.current.status).toBe("connecting"));
    expect(transport.activeSub?.eventName).toBe("runtime_event");
    expect(transport.activeSub?.query).toEqual({ after_sequence: 0 });

    transport.emit(transport.activeSub, makeEvent({ sequence_no: 1 }));
    expect(result.current.status).toBe("streaming");
  });
});

describe("useRunSession — event accumulation", () => {
  it("grows append-only by reference and dedupes by sequence_no", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async () => ({
      runs: [{ run_id: "run-1", status: "running" }],
    });

    const { result } = renderRunSession(transport, {
      conversationId: "conv-1",
    });
    await waitFor(() => expect(result.current.status).toBe("connecting"));
    const sub = transport.activeSub;

    transport.emit(sub, makeEvent({ sequence_no: 1 }));
    const afterFirst = result.current.events;
    expect(afterFirst).toHaveLength(1);

    transport.emit(sub, makeEvent({ sequence_no: 2 }));
    const afterSecond = result.current.events;
    expect(afterSecond).toHaveLength(2);
    // New array reference, prior entry identity preserved (stable growth).
    expect(afterSecond).not.toBe(afterFirst);
    expect(afterSecond[0]).toBe(afterFirst[0]);

    // Duplicate sequence_no is ignored; array reference does not change.
    transport.emit(sub, makeEvent({ sequence_no: 2 }));
    expect(result.current.events).toBe(afterSecond);
    expect(result.current.latestSequenceNo).toBe(2);
  });

  it("derives runStatus from lifecycle events, falling back to the list", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async () => ({
      runs: [{ run_id: "run-1", status: "running" }],
    });

    const { result } = renderRunSession(transport, {
      conversationId: "conv-1",
    });
    await waitFor(() => expect(result.current.status).toBe("connecting"));
    // Falls back to the list entry until an event refines it.
    expect(result.current.runStatus).toBe("running");

    transport.emit(
      transport.activeSub,
      makeEvent({
        sequence_no: 9,
        event_type: "run_completed",
        activity_kind: "run",
      }),
    );
    expect(result.current.runStatus).toBe("completed");
  });
});

describe("useRunSession — error + retry (FR-3.32)", () => {
  it("surfaces an SSE error, preserves events, then resumes from the cursor", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async () => ({
      runs: [{ run_id: "run-1", status: "running" }],
    });

    const { result } = renderRunSession(transport, {
      conversationId: "conv-1",
    });
    await waitFor(() => expect(result.current.status).toBe("connecting"));

    transport.emit(transport.activeSub, makeEvent({ sequence_no: 1 }));
    transport.emit(transport.activeSub, makeEvent({ sequence_no: 5 }));
    expect(result.current.latestSequenceNo).toBe(5);

    const failedSub = transport.activeSub;
    transport.fail(failedSub, new Error("stream dropped"));
    expect(result.current.status).toBe("error");
    expect(result.current.error?.message).toBe("stream dropped");
    // Last-projected state is retained.
    expect(result.current.events).toHaveLength(2);

    act(() => {
      result.current.retry();
    });

    // A fresh subscription resumes from the highest received sequence_no.
    await waitFor(() => expect(transport.activeSub).not.toBe(failedSub));
    expect(failedSub?.closed).toBe(true);
    expect(transport.activeSub?.query).toEqual({ after_sequence: 5 });
    expect(result.current.status).toBe("streaming");
    expect(result.current.error).toBeNull();
    // Events survived the retry (no replay/reset).
    expect(result.current.events).toHaveLength(2);
  });

  it("reports error when run resolution fails and no run is selected", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async () => {
      throw new Error("list failed");
    };

    const { result } = renderRunSession(transport, {
      conversationId: "conv-1",
    });

    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.error?.message).toBe("list failed");
    expect(result.current.runId).toBeNull();
  });
});

describe("useRunSession — multi-run selection (FR-3.26)", () => {
  it("rebinds the stream to the selected run and resets accumulated events", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async () => ({
      runs: [
        {
          run_id: "run-a",
          status: "running",
          started_at: "2026-01-02T00:00:00Z",
        },
        {
          run_id: "run-b",
          status: "completed",
          started_at: "2026-01-01T00:00:00Z",
        },
      ],
    });

    const { result } = renderRunSession(transport, {
      conversationId: "conv-1",
    });
    await waitFor(() => expect(result.current.runId).toBe("run-a"));
    const subA = transport.activeSub;
    transport.emit(subA, makeEvent({ sequence_no: 1, run_id: "run-a" }));
    expect(result.current.events).toHaveLength(1);

    act(() => {
      result.current.selectRun("run-b");
    });

    expect(result.current.runId).toBe("run-b");
    // Old subscription closed, new one bound to run-b, events reset.
    await waitFor(() =>
      expect(transport.activeSub?.path).toBe("/v1/agent/runs/run-b/stream"),
    );
    expect(subA?.closed).toBe(true);
    expect(result.current.events).toHaveLength(0);
    expect(result.current.latestSequenceNo).toBe(0);
  });
});

describe("useRunSession — enabled gate", () => {
  it("neither resolves nor subscribes when disabled", async () => {
    const transport = new FakeTransport();
    const spy = vi.fn(async () => ({ runs: [{ run_id: "run-1" }] }));
    transport.requestHandler = spy;

    const { result } = renderRunSession(transport, {
      conversationId: "conv-1",
      enabled: false,
    });

    await waitFor(() => expect(result.current.status).toBe("idle"));
    expect(spy).not.toHaveBeenCalled();
    expect(transport.subs).toHaveLength(0);
  });
});
