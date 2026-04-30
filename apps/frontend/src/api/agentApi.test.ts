import type { RuntimeEventEnvelope } from "@enterprise-search/api-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RuntimeStreamProtocolError, streamRunEvents } from "./agentApi";

class FakeEventSource {
  static instances: FakeEventSource[] = [];

  readonly listeners = new Map<string, (event: Event) => void>();
  readonly url: string;
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListener): void {
    this.listeners.set(type, listener as (event: Event) => void);
  }

  close(): void {
    this.closed = true;
  }

  emit(type: string, data = ""): void {
    this.listeners.get(type)?.({ data } as MessageEvent);
  }
}

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

describe("streamRunEvents", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  it("opens an EventSource and emits valid runtime events", () => {
    const onEvent = vi.fn();

    streamRunEvents({
      runId: "run_123",
      afterSequence: 7,
      identity: { orgId: "org_123", userId: "user_123" },
      onEvent,
      onError: vi.fn(),
    });

    expect(FakeEventSource.instances[0].url).toContain(
      "/v1/agent/runs/run_123/stream?",
    );
    expect(FakeEventSource.instances[0].url).toContain("after_sequence=7");

    const event = runtimeEvent();
    FakeEventSource.instances[0].emit("runtime_event", JSON.stringify(event));

    expect(onEvent).toHaveBeenCalledWith(event);
  });

  it("reports malformed JSON through the protocol error callback", () => {
    const onProtocolError = vi.fn();

    streamRunEvents({
      runId: "run_123",
      identity: { orgId: "org_123", userId: "user_123" },
      onEvent: vi.fn(),
      onError: vi.fn(),
      onProtocolError,
    });

    FakeEventSource.instances[0].emit("runtime_event", "{not-json");

    expect(onProtocolError).toHaveBeenCalledWith(
      expect.any(RuntimeStreamProtocolError),
    );
    expect(onProtocolError.mock.calls[0][0].reason).toBe("malformed_json");
  });

  it("reports invalid envelopes without calling onEvent", () => {
    const onEvent = vi.fn();
    const onProtocolError = vi.fn();

    streamRunEvents({
      runId: "run_123",
      identity: { orgId: "org_123", userId: "user_123" },
      onEvent,
      onError: vi.fn(),
      onProtocolError,
    });

    FakeEventSource.instances[0].emit(
      "runtime_event",
      JSON.stringify({ ok: true }),
    );

    expect(onEvent).not.toHaveBeenCalled();
    expect(onProtocolError.mock.calls[0][0].reason).toBe("invalid_envelope");
  });
});
