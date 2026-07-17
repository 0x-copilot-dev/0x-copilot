// Typed wrappers for the Phase 3 Todos destination.
//
// Surfaces:
//   1. `fetchTodos(identity, opts)`           — GET /v1/todos with
//      filter[<axis>]= repeatable params + cursor pagination
//      (sub-PRD §4.2, §8).
//   2. `createTodo / updateTodo / deleteTodo / bulkTodos`
//                                              — PATCH/POST/DELETE CRUD
//      (sub-PRD §4.3). The public POST always sets source = { kind:"user" };
//      callers never set source themselves (server rejects non-user).
//   3. `fetchPendingExtractions / acceptExtraction / rejectExtraction /
//       snoozeExtraction`                      — extraction banner flow
//      (sub-PRD §3.7, §4.3).
//
// Network rule (CLAUDE.md / `apps/frontend/CLAUDE.md`): apps call the
// **facade** only (`/v1/*`). Never `backend:8100` or `ai-backend:8000`
// directly. The transport singleton enforces this via the same-origin
// Vite proxy → facade.
//
// Wire types come from `@0x-copilot/api-types/src/todos.ts`
// once Phase 3 Impl-A lands. Today they live in `./_todos-stub` so the
// frontend wave can run in parallel.
//
// TODO(merge): swap every `./_todos-stub` import for
// `@0x-copilot/api-types`.

import type { RequestIdentity } from "./config";
import { httpDelete, httpGet, httpPatchQuery, httpPostQuery } from "./http";
import type {
  AcceptExtractionRequest,
  AcceptExtractionResponse,
  BulkTodoAction,
  BulkTodoResponse,
  CreateTodoRequest,
  ListExtractionsResponse,
  ListTodosFilters,
  ListTodosResponse,
  SnoozeExtractionRequest,
  Todo,
  TodoExtraction,
  TodoExtractionId,
  TodoId,
  TodoSortKey,
  UpdateTodoRequest,
} from "./_todos-stub";

// ===========================================================================
// LIST
// ===========================================================================

export interface FetchTodosOptions {
  readonly filters?: ListTodosFilters;
  readonly q?: string;
  readonly sort?: TodoSortKey;
  /** Opaque cursor from a previous response's `next_cursor`. */
  readonly after?: string;
  /** 1..200; server default 50. Larger values produce 400 server-side. */
  readonly limit?: number;
}

/**
 * GET /v1/todos with allowlisted filters + cursor pagination.
 *
 * Filter encoding follows sub-PRD §4.2 — `filter[<axis>]=value` keys,
 * repeated once per value. We emit one query param per filter value so
 * the server's allowlist branches per-key (any unknown axis → 400).
 *
 * Section bucketing happens client-side per sub-PRD §8; the server
 * returns a flat list (sorted by `sort`).
 */
export function fetchTodos(
  identity: RequestIdentity,
  options: FetchTodosOptions = {},
): Promise<ListTodosResponse> {
  const params = encodeListParams(options);
  return httpGet<ListTodosResponse>("/v1/todos", identity, params);
}

/**
 * GET /v1/todos/extractions?status=pending — the banner-feed query.
 * Other statuses (`accepted`, `rejected`, `snoozed`) are returned by
 * passing `status` explicitly; the destination uses pending-only.
 */
export function fetchTodoExtractions(
  identity: RequestIdentity,
  options: {
    readonly status?: TodoExtraction["status"];
    readonly after?: string;
    readonly limit?: number;
  } = {},
): Promise<ListExtractionsResponse> {
  const params: Record<string, string | undefined> = {
    status: options.status ?? "pending",
  };
  if (options.after !== undefined) {
    params.after = options.after;
  }
  if (options.limit !== undefined) {
    params.limit = String(options.limit);
  }
  return httpGet<ListExtractionsResponse>(
    "/v1/todos/extractions",
    identity,
    params,
  );
}

// ===========================================================================
// CRUD
// ===========================================================================

/** POST /v1/todos — always `source: { kind: "user" }` (server-enforced). */
export function createTodo(
  identity: RequestIdentity,
  body: CreateTodoRequest,
): Promise<Todo> {
  return httpPostQuery<Todo>("/v1/todos", body, identity);
}

export function updateTodo(
  identity: RequestIdentity,
  id: TodoId,
  body: UpdateTodoRequest,
): Promise<Todo> {
  return httpPatchQuery<Todo>(
    `/v1/todos/${encodeURIComponent(id)}`,
    body,
    identity,
  );
}

export function deleteTodo(
  identity: RequestIdentity,
  id: TodoId,
): Promise<void> {
  return httpDelete(`/v1/todos/${encodeURIComponent(id)}`, identity);
}

/**
 * POST /v1/todos/bulk — single transaction; one audit row per affected
 * todo with a shared `correlation_id` (sub-PRD §6).
 */
export function bulkTodos(
  identity: RequestIdentity,
  body: BulkTodoAction,
): Promise<BulkTodoResponse> {
  return httpPostQuery<BulkTodoResponse>("/v1/todos/bulk", body, identity);
}

// ===========================================================================
// Extraction lifecycle (sub-PRD §3.7)
// ===========================================================================

export function acceptExtraction(
  identity: RequestIdentity,
  id: TodoExtractionId,
  body: AcceptExtractionRequest,
): Promise<AcceptExtractionResponse> {
  return httpPostQuery<AcceptExtractionResponse>(
    `/v1/todos/extractions/${encodeURIComponent(id)}/accept`,
    body,
    identity,
  );
}

export function rejectExtraction(
  identity: RequestIdentity,
  id: TodoExtractionId,
): Promise<{ readonly id: TodoExtractionId; readonly status: "rejected" }> {
  return httpPostQuery(
    `/v1/todos/extractions/${encodeURIComponent(id)}/reject`,
    {},
    identity,
  );
}

export function snoozeExtraction(
  identity: RequestIdentity,
  id: TodoExtractionId,
  body: SnoozeExtractionRequest,
): Promise<{
  readonly id: TodoExtractionId;
  readonly status: "snoozed";
  readonly snoozed_until: string;
}> {
  return httpPostQuery(
    `/v1/todos/extractions/${encodeURIComponent(id)}/snooze`,
    body,
    identity,
  );
}

// ===========================================================================
// Helpers
// ===========================================================================

/**
 * Encode `FetchTodosOptions` into the flat `{key: string}` map the
 * shared `httpGet` helper builds query strings from. We collapse
 * repeated `filter[axis]` values to comma-separated lists to fit the
 * flat shape — the facade accepts both `filter[priority]=high&filter[priority]=med`
 * AND `filter[priority]=high,med` per sub-PRD §4.2 (allowlisted axes).
 * Comma is the simpler, single-key encoding.
 */
function encodeListParams(
  options: FetchTodosOptions,
): Record<string, string | undefined> {
  const params: Record<string, string | undefined> = {};
  const { filters, q, sort, after, limit } = options;

  if (filters?.done !== undefined) {
    params["filter[done]"] = filters.done ? "true" : "false";
  }
  if (filters?.priority && filters.priority.length > 0) {
    params["filter[priority]"] = filters.priority.join(",");
  }
  if (filters?.project_id && filters.project_id.length > 0) {
    params["filter[project_id]"] = filters.project_id.join(",");
  }
  if (filters?.source && filters.source.length > 0) {
    params["filter[source]"] = filters.source.join(",");
  }
  if (q !== undefined && q.length > 0) {
    params.q = q;
  }
  if (sort !== undefined) {
    params.sort = sort;
  }
  if (after !== undefined) {
    params.after = after;
  }
  if (limit !== undefined) {
    params.limit = String(limit);
  }
  return params;
}
