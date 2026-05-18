import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HomePayload } from "@enterprise-search/api-types";

import { fetchHome, openHomeStream, type HomeStreamEnvelope } from "./homeApi";
import { configureAuthBearerProvider } from "./http";

// Identity used in every test — keeps cases comparable.
const IDENTITY = { orgId: "org_test", userId: "user_test" };

/**
 * Minimal Phase 9 `HomePayload` — every required field at the wire
 * defaults so contract drift surfaces as a TS failure rather than a
 * runtime surprise.
 */
function homePayload(): HomePayload {
  return {
    greeting: {
      display_name: "Sarah",
      time_segment: "morning",
      tenant_local_date: "2026-05-18",
      tenant_local_iso: "2026-05-18T09:00:00Z",
    },
    triage: {
      approvals_waiting: 0,
      runs_failed_24h: 0,
      todos_overdue: 0,
      todos_due_today: 0,
    },
    today_timeline: { status: "ok", data: [] },
    whats_new: {
      status: "ok",
      since_iso: "2026-05-18T07:42:00Z",
      data: [],
    },
    in_flight_projects: { status: "ok", data: [] },
    live_activity: { status: "ok", data: [] },
    quick_actions: [],
    cached_at: "2026-05-18T09:00:00Z",
    is_first_run: false,
  };
}

// SSE frame builder — matches the wire format the facade emits.
function sseFrame(eventName: string, data: string): string {
  return `event: ${eventName}\ndata: ${data}\n\n`;
}

function streamingResponse(chunks: readonly string[]): Response {
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

async function flushMicrotasks(): Promise<void> {
  for (let i = 0; i < 5; i++) {
    await Promise.resolve();
  }
}

describe("fetchHome (Phase 9)", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/home with identity", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) => {
      return new Response(JSON.stringify(homePayload()), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchHome(IDENTITY);

    expect(result.greeting.display_name).toBe("Sarah");
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/home");
    expect(url).toContain("org_id=org_test");
    expect(url).toContain("user_id=user_test");
    // Facade-only: relative `/v1/*` path, never an absolute backend URL.
    expect(url).not.toContain(":8100");
    expect(url).not.toContain(":8000");
  });

  it("surfaces facade errors as rejected promises", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        return new Response(
          JSON.stringify({ detail: "home aggregator unavailable" }),
          { status: 503, headers: { "content-type": "application/json" } },
        );
      }),
    );

    await expect(fetchHome(IDENTITY)).rejects.toThrow(
      /home aggregator unavailable/,
    );
  });
});

describe("openHomeStream (Phase 9)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("opens /v1/home/stream and emits typed envelopes", async () => {
    const envelope: HomeStreamEnvelope = {
      type: "home.triage_updated",
      sequence_no: 5,
      triage: {
        approvals_waiting: 3,
        runs_failed_24h: 0,
        todos_overdue: 0,
        todos_due_today: 1,
      },
    };
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) =>
      streamingResponse([sseFrame("home_activity", JSON.stringify(envelope))]),
    );
    vi.stubGlobal("fetch", fetchMock);

    const onEvent = vi.fn();
    const onOpen = vi.fn();
    const onError = vi.fn();
    const handle = openHomeStream({
      identity: IDENTITY,
      afterSequence: 4,
      onEvent,
      onError,
      onOpen,
    });

    await flushMicrotasks();

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/home/stream");
    expect(url).toContain("after_sequence=4");
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onEvent).toHaveBeenCalledWith(envelope);
    expect(onError).not.toHaveBeenCalled();
    expect(typeof handle.close).toBe("function");
  });

  it("normalises legacy HomeActivityEvent frames into home.activity_appended", async () => {
    const legacy = {
      event_id: "evt_42",
      sequence_no: 42,
      event_type: "activity_added",
      row: {
        kind: "run",
        ref: { kind: "run", id: "run_1" },
        title: "Atlas drafted a brief",
        occurred_at: "2026-05-18T09:01:00Z",
      },
      created_at: "2026-05-18T09:01:00Z",
    };
    vi.stubGlobal("fetch", async () =>
      streamingResponse([sseFrame("home_activity", JSON.stringify(legacy))]),
    );

    const onEvent = vi.fn();
    openHomeStream({
      identity: IDENTITY,
      onEvent,
      onError: vi.fn(),
    });

    await flushMicrotasks();

    expect(onEvent).toHaveBeenCalledTimes(1);
    const env = onEvent.mock.calls[0][0] as HomeStreamEnvelope;
    expect(env.type).toBe("home.activity_appended");
    expect(env.sequence_no).toBe(42);
  });

  it("drops malformed JSON frames without invoking onEvent or onError", async () => {
    vi.stubGlobal("fetch", async () =>
      streamingResponse([sseFrame("home_activity", "{not-json")]),
    );

    const onEvent = vi.fn();
    const onError = vi.fn();
    openHomeStream({
      identity: IDENTITY,
      onEvent,
      onError,
    });

    await flushMicrotasks();

    expect(onEvent).not.toHaveBeenCalled();
    // Stream-level errors call onError; per-frame malformed JSON does NOT.
    expect(onError).not.toHaveBeenCalled();
  });

  it("drops frames whose envelope kind is unknown", async () => {
    vi.stubGlobal("fetch", async () =>
      streamingResponse([
        sseFrame(
          "home_activity",
          JSON.stringify({ type: "home.unknown", sequence_no: 1 }),
        ),
      ]),
    );

    const onEvent = vi.fn();
    openHomeStream({
      identity: IDENTITY,
      onEvent,
      onError: vi.fn(),
    });

    await flushMicrotasks();

    expect(onEvent).not.toHaveBeenCalled();
  });
});
