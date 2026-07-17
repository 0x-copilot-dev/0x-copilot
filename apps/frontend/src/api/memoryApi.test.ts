import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ConversationId,
  MemoryItem,
  MemoryItemId,
  MemoryListResponse,
  MemoryProposal,
  MemoryProposalListResponse,
  MemorySearchResponse,
  MemoryStreamEnvelope,
  TenantId,
  UserId,
} from "@0x-copilot/api-types";

import { configureAuthBearerProvider } from "./http";
import {
  acceptMemoryProposal,
  createMemory,
  deleteMemory,
  fetchMemory,
  fetchMemoryItem,
  fetchMemoryProposals,
  patchMemory,
  rejectMemoryProposal,
  searchMemory,
  streamMemoryEvents,
  touchMemory,
} from "./memoryApi";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

function itemFixture(overrides: Partial<MemoryItem> = {}): MemoryItem {
  return {
    id: "mem_1" as MemoryItemId,
    tenant_id: "tenant_1" as TenantId,
    scope: "user",
    kind: "skill",
    title: "Speaks Python",
    body: "Long-time Python developer.",
    tags: ["python"],
    created_by: { kind: "user", id: "user_test" },
    last_used_at: null,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
    project_id: null,
    ...overrides,
  };
}

function listFixture(items: ReadonlyArray<MemoryItem>): MemoryListResponse {
  return { items, next_cursor: null };
}

function proposalFixture(): MemoryProposal {
  return {
    id: "mp_1",
    tenant_id: "tenant_1" as TenantId,
    user_id: "user_test" as UserId,
    proposed_at: "2026-05-18T09:00:00Z",
    proposed_kind: "fact",
    proposed_title: "Q1 launch is 2026-03-15",
    proposed_body: "Mentioned in chat last Thursday.",
    source: { kind: "chat", id: "conv_42" as ConversationId },
    status: "pending",
    decided_at: null,
  };
}

function proposalListFixture(): MemoryProposalListResponse {
  return { proposals: [proposalFixture()], next_cursor: null };
}

function searchFixture(): MemorySearchResponse {
  return {
    hits: [{ item: itemFixture(), score: 0.91, snippet: "Long-time…" }],
    took_ms: 42,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function emptyResponse(status = 204): Response {
  return new Response(null, { status });
}

function fetchMockReturning(
  responder: () => Response,
): ReturnType<typeof vi.fn> {
  return vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    responder(),
  );
}

describe("fetchMemory", () => {
  beforeEach(() => configureAuthBearerProvider(() => "test-bearer"));
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/memory with identity (no filters when bare)", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(listFixture([itemFixture()])),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await fetchMemory(IDENTITY);
    expect(res.items).toHaveLength(1);

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/memory");
    expect(url).toContain("org_id=org_test");
    expect(url).not.toMatch(/^https?:\/\/(127\.0\.0\.1|localhost):(8000|8100)/);
  });

  it("encodes filter axes + q + sort + limit", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(listFixture([])));
    vi.stubGlobal("fetch", fetchMock);

    await fetchMemory(IDENTITY, {
      scope: "workspace",
      kind: "preference",
      tag: "tone",
      q: "tldr",
      sort: "last_used:desc",
      limit: 10,
    });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain(encodeURIComponent("filter[scope]") + "=workspace");
    expect(url).toContain(encodeURIComponent("filter[kind]") + "=preference");
    expect(url).toContain(encodeURIComponent("filter[tag]") + "=tone");
    expect(url).toContain("q=tldr");
    expect(url).toContain("sort=" + encodeURIComponent("last_used:desc"));
    expect(url).toContain("limit=10");
  });

  it("propagates 503 as an Error", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "memory unavailable" }, 503),
      ),
    );
    await expect(fetchMemory(IDENTITY)).rejects.toThrow("memory unavailable");
  });
});

describe("fetchMemoryItem", () => {
  beforeEach(() => configureAuthBearerProvider(() => "test-bearer"));
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/memory/{id} with URL-encoded id", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(itemFixture()));
    vi.stubGlobal("fetch", fetchMock);

    await fetchMemoryItem(IDENTITY, "mem/x 1" as MemoryItemId);

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/memory/mem%2Fx%201",
    );
  });

  it("propagates 404 as an Error", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() => jsonResponse({ detail: "not_found" }, 404)),
    );
    await expect(
      fetchMemoryItem(IDENTITY, "missing" as MemoryItemId),
    ).rejects.toThrow("not_found");
  });
});

describe("createMemory + patchMemory + deleteMemory + touchMemory", () => {
  beforeEach(() => configureAuthBearerProvider(() => "test-bearer"));
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("POSTs /v1/memory with the create body", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(itemFixture()));
    vi.stubGlobal("fetch", fetchMock);
    await createMemory(IDENTITY, {
      scope: "user",
      kind: "skill",
      title: "x",
      body: "y",
    });
    const call = fetchMock.mock.calls[0];
    expect(String(call[0])).toContain("/v1/memory");
    expect((call[1] as RequestInit).method).toBe("POST");
  });

  it("PATCHes /v1/memory/{id}", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(itemFixture({ title: "Renamed" })),
    );
    vi.stubGlobal("fetch", fetchMock);
    await patchMemory(IDENTITY, "mem_1" as MemoryItemId, { title: "Renamed" });
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("PATCH");
    expect(String(fetchMock.mock.calls[0][0])).toContain("/v1/memory/mem_1");
  });

  it("DELETEs /v1/memory/{id}", async () => {
    const fetchMock = fetchMockReturning(() => emptyResponse(204));
    vi.stubGlobal("fetch", fetchMock);
    await deleteMemory(IDENTITY, "mem_1" as MemoryItemId);
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("DELETE");
  });

  it("POSTs /v1/memory/{id}/touch", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(itemFixture()));
    vi.stubGlobal("fetch", fetchMock);
    await touchMemory(IDENTITY, "mem_1" as MemoryItemId);
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/memory/mem_1/touch",
    );
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
  });
});

describe("memory proposals", () => {
  beforeEach(() => configureAuthBearerProvider(() => "test-bearer"));
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("lists proposals with optional status filter", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(proposalListFixture()),
    );
    vi.stubGlobal("fetch", fetchMock);
    await fetchMemoryProposals(IDENTITY, { status: "pending" });
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/memory/proposals");
    expect(url).toContain(encodeURIComponent("filter[status]") + "=pending");
  });

  it("accepts a proposal with override body", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(itemFixture()));
    vi.stubGlobal("fetch", fetchMock);
    await acceptMemoryProposal(IDENTITY, "mp_1", { title_override: "Tweaked" });
    const call = fetchMock.mock.calls[0];
    expect(String(call[0])).toContain("/v1/memory/proposals/mp_1/accept");
    expect(JSON.parse((call[1] as RequestInit).body as string)).toMatchObject({
      title_override: "Tweaked",
    });
  });

  it("rejects a proposal", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({ ...proposalFixture(), status: "rejected" }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await rejectMemoryProposal(IDENTITY, "mp_1");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/memory/proposals/mp_1/reject",
    );
  });
});

describe("searchMemory", () => {
  beforeEach(() => configureAuthBearerProvider(() => "test-bearer"));
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/memory/search with q", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(searchFixture()));
    vi.stubGlobal("fetch", fetchMock);
    const res = await searchMemory(IDENTITY, { q: "python" });
    expect(res.hits).toHaveLength(1);
    expect(String(fetchMock.mock.calls[0][0])).toContain("/v1/memory/search");
    expect(String(fetchMock.mock.calls[0][0])).toContain("q=python");
  });
});

describe("streamMemoryEvents", () => {
  it("subscribes via getAppTransport and parses well-formed envelopes", async () => {
    const transport = await import("./transport");
    const subscribeSpy = vi
      .spyOn(transport.getAppTransport(), "subscribeServerSentEvents")
      .mockImplementation((opts) => {
        opts.onMessage(
          JSON.stringify({
            event_id: "evt_1",
            sequence_no: 11,
            event_type: "memory.created",
            item: itemFixture(),
            created_at: "2026-05-18T09:00:00Z",
          } satisfies MemoryStreamEnvelope),
        );
        opts.onMessage("{not-json");
        return { close: () => undefined };
      });

    const onEvent = vi.fn();
    const onError = vi.fn();
    streamMemoryEvents({
      identity: IDENTITY,
      afterSequence: 10,
      onEvent,
      onError,
    });

    const opts = subscribeSpy.mock.calls[0][0];
    expect(opts.path).toBe("/v1/memory/stream");
    expect(opts.query).toMatchObject({
      org_id: "org_test",
      user_id: "user_test",
      after_sequence: "10",
    });
    expect(opts.eventName).toBe("memory_event");

    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(onEvent.mock.calls[0][0]).toMatchObject({
      sequence_no: 11,
      event_type: "memory.created",
    });
    expect(onError).not.toHaveBeenCalled();

    subscribeSpy.mockRestore();
  });

  it("forwards transport-level errors through onError as an Event", async () => {
    const transport = await import("./transport");
    let capturedError: ((err: Error) => void) | undefined;
    const subscribeSpy = vi
      .spyOn(transport.getAppTransport(), "subscribeServerSentEvents")
      .mockImplementation((opts) => {
        capturedError = opts.onError;
        return { close: () => undefined };
      });

    const onError = vi.fn();
    streamMemoryEvents({
      identity: IDENTITY,
      onEvent: () => undefined,
      onError,
    });

    expect(capturedError).toBeDefined();
    capturedError!(new Error("network down"));

    expect(onError).toHaveBeenCalledTimes(1);
    expect((onError.mock.calls[0][0] as Event).type).toBe("error");

    subscribeSpy.mockRestore();
  });
});
