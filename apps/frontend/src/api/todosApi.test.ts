import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { configureAuthBearerProvider } from "./http";
import {
  acceptExtraction,
  bulkTodos,
  createTodo,
  deleteTodo,
  fetchTodoExtractions,
  fetchTodos,
  rejectExtraction,
  snoozeExtraction,
  updateTodo,
} from "./todosApi";
import type {
  Todo,
  TodoExtraction,
  TodoExtractionId,
  TodoId,
} from "./_todos-stub";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

/** Minimal Todo — every required field but nothing more. */
function todoFixture(overrides: Partial<Todo> = {}): Todo {
  return {
    id: "todo_1" as TodoId,
    tenant_id: "tenant_1",
    owner_user_id: "user_test",
    text: "Ship the brief",
    done: false,
    priority: "med",
    source: { kind: "user" },
    labels: [],
    sort_index: 0,
    created_at: "2026-05-18T09:00:00Z",
    updated_at: "2026-05-18T09:00:00Z",
    ...overrides,
  };
}

function extractionFixture(): TodoExtraction {
  return {
    id: "te_1" as TodoExtractionId,
    tenant_id: "tenant_1",
    owner_user_id: "user_test",
    source: {
      thread_id: "conv_1" as never,
      run_id: "run_1" as never,
    },
    proposed_todos: [{ text: "Ship", priority: "med" }],
    status: "pending",
    created_at: "2026-05-18T09:00:00Z",
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/**
 * Build a typed fetch mock — the explicit arg signature is what lets
 * `mock.calls[0][1]` narrow to `RequestInit | undefined`. Without it
 * vitest infers `[]` and tuple access errors out under strict tsc.
 */
function fetchMockReturning(
  responder: () => Response,
): ReturnType<typeof vi.fn> {
  return vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    responder(),
  );
}

describe("fetchTodos", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/todos with identity and no extras when called bare", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({ items: [todoFixture()] }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await fetchTodos(IDENTITY);

    expect(res.items).toHaveLength(1);
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/todos");
    expect(url).toContain("org_id=org_test");
    expect(url).toContain("user_id=user_test");
    // No filter/sort/limit query params on a bare call — server defaults apply.
    expect(url).not.toContain("filter%5B");
    expect(url).not.toContain("sort=");
    expect(url).not.toContain("limit=");
    // Facade-only: never an absolute backend URL.
    expect(url).not.toContain(":8100");
    expect(url).not.toContain(":8000");
  });

  it("encodes allowlisted filters, sort, search, and cursor", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({ items: [], next_cursor: "cursor_2" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchTodos(IDENTITY, {
      filters: {
        done: false,
        priority: ["high", "med"],
        project_id: ["proj_1", "unfiled"],
        source: ["chat", "agent"],
      },
      q: "legal",
      sort: "due:asc",
      after: "cursor_1",
      limit: 25,
    });

    const url = String(fetchMock.mock.calls[0][0]);
    // Each axis encoded as a single comma-list (matches §4.2 allowlist).
    expect(url).toContain("filter%5Bdone%5D=false");
    expect(url).toContain("filter%5Bpriority%5D=high%2Cmed");
    expect(url).toContain("filter%5Bproject_id%5D=proj_1%2Cunfiled");
    expect(url).toContain("filter%5Bsource%5D=chat%2Cagent");
    expect(url).toContain("q=legal");
    expect(url).toContain("sort=due%3Aasc");
    expect(url).toContain("after=cursor_1");
    expect(url).toContain("limit=25");
  });

  it("surfaces facade errors as rejected promises", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "tenant lookup failed" }, 503),
      ),
    );

    await expect(fetchTodos(IDENTITY)).rejects.toThrow(/tenant lookup failed/);
  });
});

describe("fetchTodoExtractions", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("defaults to status=pending and forwards identity", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({ items: [extractionFixture()] }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await fetchTodoExtractions(IDENTITY);

    expect(res.items).toHaveLength(1);
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/todos/extractions");
    expect(url).toContain("status=pending");
  });

  it("forwards an explicit status + cursor", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse({ items: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await fetchTodoExtractions(IDENTITY, { status: "snoozed", after: "cur" });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("status=snoozed");
    expect(url).toContain("after=cur");
  });
});

describe("CRUD mutations", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("POSTs a create with body + identity in query", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(todoFixture()));
    vi.stubGlobal("fetch", fetchMock);

    await createTodo(IDENTITY, { text: "New todo", priority: "high" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/v1/todos");
    expect(init?.method).toBe("POST");
    expect(JSON.parse((init?.body as string) ?? "{}")).toEqual({
      text: "New todo",
      priority: "high",
    });
  });

  it("PATCHes /v1/todos/<id> with the partial body", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(todoFixture({ done: true })),
    );
    vi.stubGlobal("fetch", fetchMock);

    const updated = await updateTodo(IDENTITY, "todo_1" as TodoId, {
      done: true,
    });

    expect(updated.done).toBe(true);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/v1/todos/todo_1");
    expect(init?.method).toBe("PATCH");
    expect(JSON.parse((init?.body as string) ?? "{}")).toEqual({ done: true });
  });

  it("DELETEs /v1/todos/<id> and resolves on 204", async () => {
    const fetchMock = fetchMockReturning(
      () => new Response(null, { status: 204 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      deleteTodo(IDENTITY, "todo_1" as TodoId),
    ).resolves.toBeUndefined();
    expect(fetchMock.mock.calls[0][1]?.method).toBe("DELETE");
  });

  it("POSTs a bulk action body with action + ids", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({ affected: 2, correlation_id: "corr_1" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await bulkTodos(IDENTITY, {
      action: "mark_done",
      ids: ["todo_1" as TodoId, "todo_2" as TodoId],
    });

    expect(res.affected).toBe(2);
    expect(
      JSON.parse((fetchMock.mock.calls[0][1]?.body as string) ?? "{}"),
    ).toEqual({
      action: "mark_done",
      ids: ["todo_1", "todo_2"],
    });
  });

  it("rejects when the server returns 4xx", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "invalid priority" }, 400),
      ),
    );

    await expect(
      updateTodo(IDENTITY, "todo_1" as TodoId, {
        priority: "high",
      }),
    ).rejects.toThrow(/invalid priority/);
  });
});

describe("Extraction lifecycle", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("POSTs accept with accepted_indices", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({ todos: [todoFixture()] }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await acceptExtraction(IDENTITY, "te_1" as TodoExtractionId, {
      accepted_indices: [0, 2],
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/v1/todos/extractions/te_1/accept");
    expect(JSON.parse((init?.body as string) ?? "{}")).toEqual({
      accepted_indices: [0, 2],
    });
  });

  it("POSTs reject with an empty body", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({ id: "te_1", status: "rejected" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await rejectExtraction(IDENTITY, "te_1" as TodoExtractionId);

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/v1/todos/extractions/te_1/reject");
    expect(JSON.parse((init?.body as string) ?? "{}")).toEqual({});
  });

  it("POSTs snooze with the until ISO timestamp", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({
        id: "te_1",
        status: "snoozed",
        snoozed_until: "2026-05-19T00:00:00Z",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await snoozeExtraction(IDENTITY, "te_1" as TodoExtractionId, {
      until: "2026-05-19T00:00:00Z",
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/v1/todos/extractions/te_1/snooze");
    expect(JSON.parse((init?.body as string) ?? "{}")).toEqual({
      until: "2026-05-19T00:00:00Z",
    });
  });

  it("surfaces extraction errors as rejected promises", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "extraction not found" }, 404),
      ),
    );

    await expect(
      acceptExtraction(IDENTITY, "te_missing" as TodoExtractionId, {
        accepted_indices: [0],
      }),
    ).rejects.toThrow(/extraction not found/);
  });
});
