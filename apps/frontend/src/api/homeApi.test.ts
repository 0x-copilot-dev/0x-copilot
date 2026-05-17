import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  HOME_ACTIVITY_WINDOW_HOURS_ALLOWED,
  HOME_ACTIVITY_WINDOW_HOURS_DEFAULT,
  fetchHome,
  streamHomeActivity,
} from "./homeApi";
import { configureAuthBearerProvider } from "./http";
import type { AgentActivityEntry, HomeResponse } from "./_home-stub";

// Identity used in every test — keeps cases comparable.
const IDENTITY = { orgId: "org_test", userId: "user_test" };

// Minimal HomeResponse shape — every field required by the type but
// nothing more, so a contract change shows up as a TS failure rather
// than a runtime surprise.
function homeResponse(): HomeResponse {
  return {
    greeting: {
      time_of_day: "morning",
      user_first_name: "Sarah",
      tenant_local_date: "2026-05-18",
      tenant_local_iso: "2026-05-18T09:00:00Z",
      agents_working_count: 0,
      needs_you_count: 0,
    },
    agent_activity: { status: "ok", data: [] },
    pinned_chats: { status: "ok", data: [] },
    recent_runs: { status: "ok", data: [] },
    favorite_tools: { status: "ok", data: [] },
    todays_focus: { status: "ok", data: [] },
    upcoming_meetings: null,
    starred_projects: { status: "ok", data: [] },
    quick_actions: [],
    cached_at: "2026-05-18T09:00:00Z",
  };
}

// SSE frame builder — matches the wire format the facade emits for the
// `home_activity` event. Reused across stream tests.
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

describe("fetchHome", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/home with identity + default activity window", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) => {
      return new Response(JSON.stringify(homeResponse()), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchHome(IDENTITY);

    expect(result.greeting.user_first_name).toBe("Sarah");
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/home");
    expect(url).toContain("org_id=org_test");
    expect(url).toContain("user_id=user_test");
    expect(url).toContain(
      `activity_window_hours=${HOME_ACTIVITY_WINDOW_HOURS_DEFAULT}`,
    );
    // Facade-only: relative `/v1/*` path, never an absolute backend URL.
    expect(url).not.toContain(":8100");
    expect(url).not.toContain(":8000");
  });

  it("forwards a non-default activity window value", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL) =>
        new Response(JSON.stringify(homeResponse()), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    // 168h = 1 week — the largest allowed value, exercises the
    // non-default branch.
    await fetchHome(IDENTITY, { activityWindowHours: 168 });

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "activity_window_hours=168",
    );
    // The constant has exactly the 5 values §9.5 dictates; if anyone
    // expands the allowlist, this assertion forces them to think about
    // the backend filter implications.
    expect(HOME_ACTIVITY_WINDOW_HOURS_ALLOWED).toEqual([6, 12, 24, 48, 168]);
  });

  it("forwards refresh_section for per-section retry", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL) =>
        new Response(JSON.stringify(homeResponse()), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchHome(IDENTITY, { refreshSection: "recent_runs" });

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "refresh_section=recent_runs",
    );
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

describe("streamHomeActivity", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // Minimal AgentActivityEntry frame for the structural-guard happy path.
  function activityEntry(
    overrides: Partial<AgentActivityEntry> = {},
  ): AgentActivityEntry {
    return {
      id: "act_1",
      kind: "drafted_artifact",
      agent_id: "agent_1",
      agent_name: "Atlas",
      summary: "Atlas drafted a 4-page brief.",
      created_at: "2026-05-18T09:00:00Z",
      target: { kind: "run", id: "run_1" } as AgentActivityEntry["target"],
      tone: "neutral",
      ...overrides,
    };
  }

  it("opens the home stream and emits well-formed activity entries", async () => {
    const entry = activityEntry();
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) =>
      streamingResponse([sseFrame("home_activity", JSON.stringify(entry))]),
    );
    vi.stubGlobal("fetch", fetchMock);

    const onEvent = vi.fn();
    const onOpen = vi.fn();
    const onError = vi.fn();
    const handle = streamHomeActivity({
      identity: IDENTITY,
      activityWindowHours: 48,
      onEvent,
      onError,
      onOpen,
    });

    await flushMicrotasks();

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/home/stream");
    expect(url).toContain("activity_window_hours=48");
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onEvent).toHaveBeenCalledWith(entry);
    expect(onError).not.toHaveBeenCalled();
    expect(typeof handle.close).toBe("function");
  });

  it("drops malformed JSON frames without invoking onEvent", async () => {
    vi.stubGlobal("fetch", async () =>
      streamingResponse([sseFrame("home_activity", "{not-json")]),
    );

    const onEvent = vi.fn();
    const onError = vi.fn();
    streamHomeActivity({
      identity: IDENTITY,
      onEvent,
      onError,
    });

    await flushMicrotasks();

    expect(onEvent).not.toHaveBeenCalled();
    // Stream-level errors (connection drops) call onError; per-frame
    // malformed JSON does NOT — one bad frame must not end the stream.
    expect(onError).not.toHaveBeenCalled();
  });

  it("drops frames whose discriminator shape doesn't match", async () => {
    vi.stubGlobal("fetch", async () =>
      streamingResponse([
        sseFrame("home_activity", JSON.stringify({ id: "x", ok: true })),
      ]),
    );

    const onEvent = vi.fn();
    streamHomeActivity({
      identity: IDENTITY,
      onEvent,
      onError: vi.fn(),
    });

    await flushMicrotasks();

    expect(onEvent).not.toHaveBeenCalled();
  });
});
