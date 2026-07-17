import type {
  ConnectorId,
  CreateToolRequest,
  ProjectId,
  TenantId,
  Tool,
  ToolDetailResponse,
  ToolId,
  ToolInvocation,
  ToolInvocationListResponse,
  ToolListResponse,
  ToolStreamEnvelope,
  ToolUsageResponse,
  UserId,
} from "@0x-copilot/api-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { configureAuthBearerProvider } from "./http";
import {
  createTool,
  deleteTool,
  disableTool,
  enableTool,
  fetchInvocations,
  fetchTool,
  fetchTools,
  fetchUsage,
  openToolStream,
  patchTool,
  testToolCall,
} from "./toolsApi";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

// ===========================================================================
// Fixtures
// ===========================================================================

function toolFixture(overrides: Partial<Tool> = {}): Tool {
  return {
    id: "tool_1" as ToolId,
    tenant_id: "tenant_1" as TenantId,
    name: "Send Slack message",
    description: "Posts to a channel via the Slack MCP server.",
    kind: "mcp",
    scope: "write",
    status: "enabled",
    args_schema: { type: "object" },
    returns_schema: { type: "object" },
    transport: { kind: "mcp" },
    owner_user_id: "user_test" as UserId,
    project_id: null,
    tags: [],
    usage: {
      calls_24h: 0,
      calls_30d: 0,
      p50_latency_ms_30d: null,
      success_rate_30d: null,
      last_used_at: null,
    },
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-18T09:00:00Z",
    ...overrides,
  };
}

function detailFixture(
  overrides: Partial<ToolDetailResponse> = {},
): ToolDetailResponse {
  return {
    tool: toolFixture(),
    consumers: {
      agents: [],
      routines: [],
      chats_with_grant: 0,
    },
    ...overrides,
  };
}

function listFixture(items: ReadonlyArray<Tool>): ToolListResponse {
  return { tools: items, next_cursor: null };
}

function invocationFixture(): ToolInvocation {
  return {
    id: "toolinv_1",
    tool_id: "tool_1" as ToolId,
    tenant_id: "tenant_1" as TenantId,
    run_id: "run_1" as ToolInvocation["run_id"],
    caller_kind: "chat",
    caller_ref: { kind: "chat", id: "conv_1" } as ToolInvocation["caller_ref"],
    args_summary: '{"channel":"#general"}',
    result_summary: '{"ok":true}',
    status: "ok",
    started_at: "2026-05-18T09:00:00Z",
    ended_at: "2026-05-18T09:00:00Z",
    latency_ms: 42,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function fetchMockReturning(
  responder: () => Response,
): ReturnType<typeof vi.fn> {
  return vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    responder(),
  );
}

// SSE helpers — same shape as agentApi.test.ts.
function sseFrame(eventName: string, data: string): string {
  return `event: ${eventName}\ndata: ${data}\n\n`;
}

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

async function flushMicrotasks(): Promise<void> {
  for (let i = 0; i < 10; i++) {
    await Promise.resolve();
  }
}

// ===========================================================================
// LIST — happy + error paths + filter encoding
// ===========================================================================

describe("fetchTools", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/tools with identity and no extras when called bare", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(listFixture([toolFixture()])),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await fetchTools(IDENTITY);

    expect(res.tools).toHaveLength(1);
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/tools");
    expect(url).toContain("org_id=org_test");
    expect(url).toContain("user_id=user_test");
    expect(url).not.toContain("filter%5B");
    expect(url).not.toContain("sort=");
    // Facade-only invariant: caller never sees an absolute backend URL.
    expect(url).not.toMatch(/^https?:\/\/(127\.0\.0\.1|localhost):(8000|8100)/);
  });

  it("encodes all filter axes + q + sort + cursor + limit", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(listFixture([])));
    vi.stubGlobal("fetch", fetchMock);

    await fetchTools(IDENTITY, {
      filters: {
        kind: "mcp",
        scope: "write",
        status: "enabled",
        owner_user_id: "user_owner" as UserId,
        project_id: "project_42" as ProjectId,
        connector_id: "connector_slack" as ConnectorId,
        tag: "messaging",
      },
      q: "slack",
      sort: "usage.calls_30d:desc",
      after: "cursor_xyz",
      limit: 25,
    });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain(encodeURIComponent("filter[kind]") + "=mcp");
    expect(url).toContain(encodeURIComponent("filter[scope]") + "=write");
    expect(url).toContain(encodeURIComponent("filter[status]") + "=enabled");
    expect(url).toContain(
      encodeURIComponent("filter[owner_user_id]") + "=user_owner",
    );
    expect(url).toContain(
      encodeURIComponent("filter[project_id]") + "=project_42",
    );
    expect(url).toContain(
      encodeURIComponent("filter[connector_id]") + "=connector_slack",
    );
    expect(url).toContain(encodeURIComponent("filter[tag]") + "=messaging");
    expect(url).toContain("q=slack");
    expect(url).toContain("sort=" + encodeURIComponent("usage.calls_30d:desc"));
    expect(url).toContain("after=cursor_xyz");
    expect(url).toContain("limit=25");
  });

  it("omits the q param entirely when the search string is empty", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(listFixture([])));
    vi.stubGlobal("fetch", fetchMock);

    await fetchTools(IDENTITY, { q: "" });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).not.toContain("q=");
  });

  it("surfaces a 503 server error", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "service_unavailable" }, 503),
      ),
    );

    await expect(fetchTools(IDENTITY)).rejects.toThrow("service_unavailable");
  });
});

// ===========================================================================
// DETAIL
// ===========================================================================

describe("fetchTool", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/tools/{id} with URL-encoded id", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(
        detailFixture({
          tool: toolFixture({ id: "tool/1 special" as ToolId }),
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchTool(IDENTITY, "tool/1 special" as ToolId);

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/tools/tool%2F1%20special");
  });

  it("propagates 404 as an Error (cross-audit §1.3: not found / not visible)", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() => jsonResponse({ detail: "tool_not_found" }, 404)),
    );

    await expect(fetchTool(IDENTITY, "missing" as ToolId)).rejects.toThrow(
      "tool_not_found",
    );
  });
});

// ===========================================================================
// MUTATIONS
// ===========================================================================

describe("createTool", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("POSTs /v1/tools with the create body", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(toolFixture()));
    vi.stubGlobal("fetch", fetchMock);

    const body: CreateToolRequest = {
      kind: "mcp",
      name: "Send Slack message",
      description: "Posts to a channel via the Slack MCP server.",
      scope: "write",
      args_schema: { type: "object" },
      returns_schema: { type: "object" },
      transport: { kind: "mcp" },
    };
    const res = await createTool(IDENTITY, body);

    expect(res.id).toBe("tool_1");
    const call = fetchMock.mock.calls[0];
    expect(String(call[0])).toContain("/v1/tools");
    expect((call[1] as RequestInit).method).toBe("POST");
    expect(JSON.parse((call[1] as RequestInit).body as string)).toMatchObject({
      name: "Send Slack message",
      kind: "mcp",
    });
  });
});

describe("patchTool", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("PATCHes /v1/tools/{id} with the partial body", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(toolFixture({ name: "Renamed" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await patchTool(IDENTITY, "tool_1" as ToolId, {
      name: "Renamed",
    });

    expect(res.name).toBe("Renamed");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("PATCH");
    expect(String(fetchMock.mock.calls[0][0])).toContain("/v1/tools/tool_1");
  });
});

describe("disableTool / enableTool", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("disableTool PATCHes status=disabled with optional reason", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(toolFixture({ status: "disabled" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    await disableTool(IDENTITY, "tool_1" as ToolId, "auth expired");

    const body = JSON.parse(
      (fetchMock.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body).toEqual({ status: "disabled", status_reason: "auth expired" });
  });

  it("disableTool omits status_reason when none supplied", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(toolFixture({ status: "disabled" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    await disableTool(IDENTITY, "tool_1" as ToolId);

    const body = JSON.parse(
      (fetchMock.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body).toEqual({ status: "disabled" });
  });

  it("enableTool PATCHes status=enabled", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(toolFixture({ status: "enabled" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    await enableTool(IDENTITY, "tool_1" as ToolId);

    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toEqual({ status: "enabled" });
  });
});

describe("deleteTool", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("DELETEs /v1/tools/{id}", async () => {
    const fetchMock = fetchMockReturning(
      () => new Response(null, { status: 204 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await deleteTool(IDENTITY, "tool_1" as ToolId);

    expect(String(fetchMock.mock.calls[0][0])).toContain("/v1/tools/tool_1");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("DELETE");
  });
});

// ===========================================================================
// TEST CALL
// ===========================================================================

describe("testToolCall", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("POSTs /v1/tools/{id}/test with args", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({ status: "ok", latency_ms: 7 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await testToolCall(IDENTITY, "tool_1" as ToolId, {
      args: { channel: "#general", text: "hi" },
    });

    expect(res.status).toBe("ok");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/tools/tool_1/test",
    );
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toEqual({ args: { channel: "#general", text: "hi" } });
  });

  it("surfaces a 501 not-yet-wired (P10-A2 stub state)", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "executor_not_implemented" }, 501),
      ),
    );

    await expect(
      testToolCall(IDENTITY, "tool_1" as ToolId, { args: {} }),
    ).rejects.toThrow("executor_not_implemented");
  });
});

// ===========================================================================
// INVOCATIONS
// ===========================================================================

describe("fetchInvocations", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/tools/{id}/invocations with paging + filter params", async () => {
    const response: ToolInvocationListResponse = {
      invocations: [invocationFixture()],
      next_cursor: null,
    };
    const fetchMock = fetchMockReturning(() => jsonResponse(response));
    vi.stubGlobal("fetch", fetchMock);

    await fetchInvocations(IDENTITY, "tool_1" as ToolId, {
      after: "cursor_v3",
      limit: 20,
      caller_kind: "chat",
      status: "error",
    });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/tools/tool_1/invocations");
    expect(url).toContain("after=cursor_v3");
    expect(url).toContain("limit=20");
    expect(url).toContain(encodeURIComponent("filter[caller_kind]") + "=chat");
    expect(url).toContain(encodeURIComponent("filter[status]") + "=error");
  });

  it("omits paging + filter params when not supplied", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({ invocations: [], next_cursor: null }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchInvocations(IDENTITY, "tool_1" as ToolId);

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).not.toContain("after=");
    expect(url).not.toContain("limit=");
    expect(url).not.toContain("filter%5B");
  });
});

// ===========================================================================
// USAGE
// ===========================================================================

describe("fetchUsage", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/tools/{id}/usage", async () => {
    const response: ToolUsageResponse = {
      tool_id: "tool_1" as ToolId,
      windows: {
        window_24h: {
          calls_24h: 0,
          calls_30d: 0,
          p50_latency_ms_30d: null,
          success_rate_30d: null,
          last_used_at: null,
        },
        window_7d: {
          calls_24h: 0,
          calls_30d: 0,
          p50_latency_ms_30d: null,
          success_rate_30d: null,
          last_used_at: null,
        },
        window_30d: {
          calls_24h: 0,
          calls_30d: 0,
          p50_latency_ms_30d: null,
          success_rate_30d: null,
          last_used_at: null,
        },
      },
    };
    const fetchMock = fetchMockReturning(() => jsonResponse(response));
    vi.stubGlobal("fetch", fetchMock);

    await fetchUsage(IDENTITY, "tool_1" as ToolId);

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/tools/tool_1/usage");
  });

  it("propagates 403 when caller cannot read the tool", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() => jsonResponse({ detail: "forbidden" }, 403)),
    );

    await expect(fetchUsage(IDENTITY, "tool_1" as ToolId)).rejects.toThrow(
      "forbidden",
    );
  });
});

// ===========================================================================
// SSE — envelope parse + malformed-frame drop
// ===========================================================================

describe("openToolStream", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  function envelopeFixture(
    overrides: Partial<ToolStreamEnvelope> = {},
  ): ToolStreamEnvelope {
    return {
      event_id: "evt_1",
      sequence_no: 1,
      event_type: "tool.created",
      tool: toolFixture(),
      created_at: "2026-05-18T09:00:00Z",
      ...overrides,
    };
  }

  it("opens /v1/tools/stream with identity + after_sequence + emits parsed envelope", async () => {
    const envelope = envelopeFixture();
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) =>
      streamingResponse([sseFrame("tool_event", JSON.stringify(envelope))]),
    );
    vi.stubGlobal("fetch", fetchMock);

    const onEvent = vi.fn();
    const onOpen = vi.fn();
    openToolStream({
      identity: IDENTITY,
      afterSequence: 7,
      onEvent,
      onError: vi.fn(),
      onOpen,
    });

    await flushMicrotasks();

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/tools/stream");
    expect(url).toContain("org_id=org_test");
    expect(url).toContain("user_id=user_test");
    expect(url).toContain("after_sequence=7");
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onEvent).toHaveBeenCalledWith(envelope);
  });

  it("drops malformed JSON frames silently (no onEvent / no tear-down)", async () => {
    const valid = envelopeFixture({ event_id: "evt_b", sequence_no: 2 });
    const wire =
      sseFrame("tool_event", "{not-json") +
      sseFrame("tool_event", JSON.stringify(valid));
    vi.stubGlobal("fetch", async () => streamingResponse([wire]));

    const onEvent = vi.fn();
    const onError = vi.fn();
    openToolStream({
      identity: IDENTITY,
      onEvent,
      onError,
    });

    await flushMicrotasks();

    // Bad frame dropped, valid one flowed through, connection not torn down.
    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(onEvent).toHaveBeenCalledWith(valid);
    expect(onError).not.toHaveBeenCalled();
  });

  it("drops envelopes that fail the structural shape check", async () => {
    const malformed = { event_id: "evt_x" }; // missing sequence_no etc.
    vi.stubGlobal("fetch", async () =>
      streamingResponse([sseFrame("tool_event", JSON.stringify(malformed))]),
    );

    const onEvent = vi.fn();
    openToolStream({
      identity: IDENTITY,
      onEvent,
      onError: vi.fn(),
    });

    await flushMicrotasks();

    expect(onEvent).not.toHaveBeenCalled();
  });

  it("omits after_sequence when not supplied", async () => {
    const fetchMock = vi.fn(async () => streamingResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    openToolStream({
      identity: IDENTITY,
      onEvent: vi.fn(),
      onError: vi.fn(),
    });

    await flushMicrotasks();

    const calls = fetchMock.mock.calls as unknown as Array<readonly unknown[]>;
    expect(calls.length).toBeGreaterThan(0);
    const url = String(calls[0][0]);
    expect(url).not.toContain("after_sequence=");
  });
});
