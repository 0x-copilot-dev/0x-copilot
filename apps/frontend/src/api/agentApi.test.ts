import type { RuntimeEventEnvelope } from "@enterprise-search/api-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  RuntimeStreamProtocolError,
  replayRunEvents,
  streamRunEvents,
} from "./agentApi";

function runtimeEvent(
  overrides: Partial<RuntimeEventEnvelope> = {},
): RuntimeEventEnvelope {
  return {
    event_id: "event_123",
    run_id: "run_123",
    conversation_id: "conversation_123",
    sequence_no: 1,
    event_type: "model_delta",
    activity_kind: "message",
    payload: { delta: "Hello" },
    created_at: "2026-04-30T00:00:00Z",
    ...overrides,
  };
}

// Build an SSE-formatted text body for a given runtime_event payload.
// Matches the wire format the facade emits: `event: runtime_event\n` then
// one or more `data: …\n` lines, terminated by a blank line.
function sseFrame(eventName: string, data: string): string {
  return `event: ${eventName}\ndata: ${data}\n\n`;
}

// Build a Response whose body is a ReadableStream that emits the given
// chunks of SSE text in order, then closes. Mirrors what the browser
// hands back from a real fetch against an SSE endpoint.
function streamingResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

// Yield to the microtask queue so the async fetch+reader loop in the
// SSE helper has a chance to run before the test assertions.
async function flushMicrotasks(): Promise<void> {
  for (let i = 0; i < 5; i++) {
    await Promise.resolve();
  }
}

describe("streamRunEvents", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("opens a stream against the run URL and emits valid runtime events", async () => {
    const event = runtimeEvent();
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) => {
      return streamingResponse([
        sseFrame("runtime_event", JSON.stringify(event)),
      ]);
    });
    vi.stubGlobal("fetch", fetchMock);

    const onEvent = vi.fn();
    const onOpen = vi.fn();
    streamRunEvents({
      runId: "run_123",
      afterSequence: 7,
      identity: { orgId: "org_123", userId: "user_123" },
      onEvent,
      onError: vi.fn(),
      onOpen,
    });

    await flushMicrotasks();

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/agent/runs/run_123/stream?",
    );
    expect(String(fetchMock.mock.calls[0][0])).toContain("after_sequence=7");
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onEvent).toHaveBeenCalledWith(event);
  });

  it("reports malformed JSON through the protocol error callback", async () => {
    vi.stubGlobal("fetch", async () =>
      streamingResponse([sseFrame("runtime_event", "{not-json")]),
    );

    const onProtocolError = vi.fn();
    streamRunEvents({
      runId: "run_123",
      identity: { orgId: "org_123", userId: "user_123" },
      onEvent: vi.fn(),
      onError: vi.fn(),
      onProtocolError,
    });

    await flushMicrotasks();

    expect(onProtocolError).toHaveBeenCalledWith(
      expect.any(RuntimeStreamProtocolError),
    );
    expect(onProtocolError.mock.calls[0][0].reason).toBe("malformed_json");
  });

  it("reports invalid envelopes without calling onEvent", async () => {
    vi.stubGlobal("fetch", async () =>
      streamingResponse([
        sseFrame("runtime_event", JSON.stringify({ ok: true })),
      ]),
    );

    const onEvent = vi.fn();
    const onProtocolError = vi.fn();
    streamRunEvents({
      runId: "run_123",
      identity: { orgId: "org_123", userId: "user_123" },
      onEvent,
      onError: vi.fn(),
      onProtocolError,
    });

    await flushMicrotasks();

    expect(onEvent).not.toHaveBeenCalled();
    expect(onProtocolError.mock.calls[0][0].reason).toBe("invalid_envelope");
  });

  it("dispatches multiple frames split across chunk boundaries", async () => {
    const first = runtimeEvent({ event_id: "event_a", sequence_no: 1 });
    const second = runtimeEvent({ event_id: "event_b", sequence_no: 2 });
    const wire =
      sseFrame("runtime_event", JSON.stringify(first)) +
      sseFrame("runtime_event", JSON.stringify(second));
    // Split mid-frame — the parser must buffer across chunks.
    const splitAt = Math.floor(wire.length / 2);
    vi.stubGlobal("fetch", async () =>
      streamingResponse([wire.slice(0, splitAt), wire.slice(splitAt)]),
    );

    const onEvent = vi.fn();
    streamRunEvents({
      runId: "run_123",
      identity: { orgId: "org_123", userId: "user_123" },
      onEvent,
      onError: vi.fn(),
    });

    await flushMicrotasks();

    expect(onEvent).toHaveBeenCalledTimes(2);
    expect(onEvent).toHaveBeenNthCalledWith(1, first);
    expect(onEvent).toHaveBeenNthCalledWith(2, second);
  });
});

describe("replayRunEvents", () => {
  it("fetches persisted runtime events for a run", async () => {
    const event = runtimeEvent();
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) => {
      return new Response(
        JSON.stringify({
          run_id: "run_123",
          events: [event],
          latest_sequence_no: 1,
          run_status: "completed",
          has_more: false,
        }),
        { status: 200 },
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const replay = await replayRunEvents(
      "run_123",
      { orgId: "org_123", userId: "user_123" },
      3,
    );

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/agent/runs/run_123/events?",
    );
    expect(String(fetchMock.mock.calls[0][0])).toContain("after_sequence=3");
    expect(replay.events).toEqual([event]);
  });
});
