// useRunSession — host-hook unit tests (PR-3.3).
//
// Everything the hook touches is a Transport-port call, so the whole surface
// is exercised through a purpose-built in-memory fake: `FakeTransport`
// implements `Transport`, resolves the conversation head (`latest_run_id` /
// `latest_run_id_any_status`, from `GET /v1/agent/conversations/{id}`) via a
// swappable handler, and captures every SSE subscription so a test can drive
// `onMessage`/`onError` by hand and assert the resume cursor (`?after_sequence=N`)
// on re-subscribe. The dead `GET /v1/agent/runs` auto-resolve (a POST-only route
// → 405) is gone; run resolution is the conversation head only (desktop-run-identity §D2).

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
  // Neutral default: a conversation head with no run → the idle/empty cockpit.
  requestHandler: (req: TypedRequest) => Promise<unknown> = async () => ({});
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
  it("binds the conversation head run resolved from GET /v1/agent/conversations/{id}", async () => {
    const transport = new FakeTransport();
    // The head projection carries the live run id (desktop-run-identity §D2).
    transport.requestHandler = async () => ({ latest_run_id: "run-1" });

    const { result } = renderRunSession(transport, {
      conversationId: "conv-1",
    });

    await waitFor(() => expect(result.current.runId).toBe("run-1"));
    const headRequest = transport.requests.find(
      (req) => req.path === "/v1/agent/conversations/conv-1",
    );
    expect(headRequest?.method).toBe("GET");
    // The dead runs-list route (POST-only → 405) is never called anymore.
    expect(
      transport.requests.some((req) => req.path === "/v1/agent/runs"),
    ).toBe(false);
    // The multi-run list stays empty until the runs-list endpoint lands (Phase 6).
    expect(result.current.runs).toEqual([]);
  });

  it("falls back to latest_run_id_any_status for a reopened finished conversation", async () => {
    const transport = new FakeTransport();
    // A finished conversation has no live run (`latest_run_id: null`), but its
    // last run survives on the any-status head — so reopening it still binds +
    // streams that run (kills the "NO ACTIVE RUN" reopen bug, Bug 3).
    transport.requestHandler = async () => ({
      latest_run_id: null,
      latest_run_id_any_status: "run-done",
    });

    const { result } = renderRunSession(transport, {
      conversationId: "conv-1",
    });

    await waitFor(() => expect(result.current.runId).toBe("run-done"));
    await waitFor(() =>
      expect(transport.activeSub?.path).toBe("/v1/agent/runs/run-done/stream"),
    );
  });

  it("reports idle with no run when the conversation head has no run", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async () => ({}); // no latest_run_id / any-status

    const { result } = renderRunSession(transport, {
      conversationId: "conv-1",
    });

    await waitFor(() => expect(result.current.status).toBe("idle"));
    expect(result.current.runId).toBeNull();
    expect(transport.subs).toHaveLength(0);
  });

  it("streams an explicit runId even before the head resolves (empty→live)", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async () => ({}); // head carries no run

    const { result } = renderRunSession(transport, {
      conversationId: "conv-1",
      runId: "run-new",
    });

    expect(result.current.runId).toBe("run-new");
    await waitFor(() => expect(transport.activeSub).toBeDefined());
    expect(transport.activeSub?.path).toBe("/v1/agent/runs/run-new/stream");
  });

  it("a fresh bind after a manual selectRun wins — no precedence trap (§D3)", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async () => ({ latest_run_id: "run-head" });

    const { result } = renderRunSession(transport, {
      conversationId: "conv-1",
    });
    // Head resolution binds the conversation's current run…
    await waitFor(() => expect(result.current.runId).toBe("run-head"));

    // …a manual selection rebinds…
    act(() => {
      result.current.selectRun("run-b");
    });
    expect(result.current.runId).toBe("run-b");

    // …and a later dispatch (bindRun) STILL wins over that selection. The old
    // `selectedRunId ?? explicitRunId ?? autoResolved` precedence would have
    // shadowed the fresh run; the single `boundRunId` sink makes last-write-win,
    // so a send after a manual pick never runs unbound (Bug 1).
    act(() => {
      result.current.bindRun("run-dispatched");
    });
    expect(result.current.runId).toBe("run-dispatched");
    await waitFor(() =>
      expect(transport.activeSub?.path).toBe(
        "/v1/agent/runs/run-dispatched/stream",
      ),
    );
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
      resolveRuns({ latest_run_id: "run-1" });
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
    transport.requestHandler = async () => ({ latest_run_id: "run-1" });

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

  it("derives runStatus from lifecycle events (the head carries no status to fall back to this phase)", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async () => ({ latest_run_id: "run-1" });

    const { result } = renderRunSession(transport, {
      conversationId: "conv-1",
    });
    await waitFor(() => expect(result.current.status).toBe("connecting"));
    // The head field carries only a run id — no status — and `runs` is empty
    // this phase, so runStatus is null until a lifecycle event refines it (the
    // runs-list status fallback lands in Phase 6).
    expect(result.current.runStatus).toBeNull();

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
    transport.requestHandler = async () => ({ latest_run_id: "run-1" });

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

  it("surfaces a retryable error when head resolution fails, then binds on retry", async () => {
    const transport = new FakeTransport();
    let failNext = true;
    transport.requestHandler = async () => {
      if (failNext) {
        throw new Error("head fetch failed");
      }
      return { latest_run_id: "run-1" };
    };

    const { result } = renderRunSession(transport, {
      conversationId: "conv-1",
    });

    // Run resolution is now the conversation-head GET — a core endpoint, not the
    // old best-effort runs-list — so a failure surfaces as a non-blocking,
    // retryable error: no run bound, no stream opened, `runs` empty. (The cockpit
    // still lets the user start a run: RunDestination shows the empty composer
    // regardless of this error, and the banner is non-blocking — see
    // RunDestination's error-banner test.)
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.error?.message).toBe("head fetch failed");
    expect(result.current.runId).toBeNull();
    expect(result.current.runs).toEqual([]);
    expect(transport.subs).toHaveLength(0);

    // Retry re-resolves the head; this time it binds + streams the run.
    failNext = false;
    act(() => {
      result.current.retry();
    });
    await waitFor(() => expect(result.current.runId).toBe("run-1"));
    expect(result.current.error).toBeNull();
    expect(transport.activeSub?.path).toBe("/v1/agent/runs/run-1/stream");
  });
});

describe("useRunSession — multi-run selection (FR-3.26)", () => {
  it("rebinds the stream to the selected run and resets accumulated events", async () => {
    const transport = new FakeTransport();
    // The head binds run-a; a manual `selectRun` then rebinds to another run id
    // directly (multi-run selection funnels through the same `boundRunId` sink —
    // `session.runs` stays empty until the runs-list endpoint lands, Phase 6).
    transport.requestHandler = async () => ({ latest_run_id: "run-a" });

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
    const spy = vi.fn(async () => ({ latest_run_id: "run-1" }));
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
