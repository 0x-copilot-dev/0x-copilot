// Typed wrappers for the Phase 10 Tools destination (the 11th
// destination per `docs/atlas-new-design/destinations/tools-prd.md`).
//
// Surfaces (tools-prd §4):
//   1. `fetchTools(identity, opts)`                — GET  /v1/tools.
//   2. `fetchTool(identity, id)`                    — GET  /v1/tools/{id}.
//   3. `createTool / patchTool`                     — tool CRUD.
//   4. `disableTool / enableTool / deleteTool`      — status transitions
//                                                     (soft delete preserves
//                                                     audit + grants).
//   5. `testToolCall(identity, id, body)`           — POST /v1/tools/{id}/test
//                                                     (sandbox executor lands in
//                                                     P10-A3; returns 501 until).
//   6. `fetchInvocations(identity, id, opts)`       — invocation audit lens.
//   7. `fetchUsage(identity, id)`                   — read-only projection.
//   8. `openToolStream({...})`                      — SSE durable channel.
//
// Network rule (CLAUDE.md / `apps/frontend/CLAUDE.md`): apps call the
// **facade** only (`/v1/*`). Never `backend:8100` or `ai-backend:8000`
// directly. The transport singleton enforces this via the same-origin
// Vite proxy → facade.
//
// Wire types are imported from `@0x-copilot/api-types` — single
// declaration site (`packages/api-types/src/tools.ts`).

import type {
  ConnectorId,
  CreateToolRequest,
  ProjectId,
  TestToolCallRequest,
  TestToolCallResponse,
  Tool,
  ToolDetailResponse,
  ToolId,
  ToolInvocationListResponse,
  ToolKind,
  ToolListResponse,
  ToolScope,
  ToolStatus,
  ToolStreamEnvelope,
  ToolUsageResponse,
  UpdateToolRequest,
  UserId,
} from "@0x-copilot/api-types";

import type { RequestIdentity } from "./config";
import { httpDelete, httpGet, httpPatchQuery, httpPostQuery } from "./http";
import { getAppTransport } from "./transport";

/** Central base path constant — single source of truth for the Tools route. */
const TOOLS_BASE = "/v1/tools";

/** SSE event name — must match the server's `event: <name>` line. */
const SSE_EVENT_NAME = "tool_event";

// ===========================================================================
// LIST
// ===========================================================================

/**
 * Allowlisted filter axes for `GET /v1/tools` (tools-prd §4.1).
 *
 * The server allowlist disallows repeated axes per cross-audit §1.5 — one
 * value per axis at the wire boundary. The `tag` axis accepts a single tag
 * string and is matched server-side against the row's `tags` array.
 */
export interface ListToolsFilters {
  readonly kind?: ToolKind;
  readonly scope?: ToolScope;
  readonly status?: ToolStatus;
  readonly owner_user_id?: UserId;
  readonly project_id?: ProjectId;
  readonly connector_id?: ConnectorId;
  readonly tag?: string;
}

export type ToolSortKey =
  | "name:asc"
  | "name:desc"
  | "created_at:asc"
  | "created_at:desc"
  | "updated_at:asc"
  | "updated_at:desc"
  | "usage.calls_30d:asc"
  | "usage.calls_30d:desc"
  | "usage.last_used_at:asc"
  | "usage.last_used_at:desc";

export interface FetchToolsOptions {
  readonly filters?: ListToolsFilters;
  readonly q?: string;
  readonly sort?: ToolSortKey;
  /** Opaque cursor from a previous response's `next_cursor`. */
  readonly after?: string;
  /** 1..200; server default 50. */
  readonly limit?: number;
}

/**
 * GET /v1/tools with allowlisted filters + cursor pagination
 * (tools-prd §4.1). Filter encoding mirrors the agents / routines /
 * projects APIs — `filter[<axis>]=<value>` keys, single value per axis.
 */
export function fetchTools(
  identity: RequestIdentity,
  options: FetchToolsOptions = {},
): Promise<ToolListResponse> {
  return httpGet<ToolListResponse>(
    TOOLS_BASE,
    identity,
    encodeListParams(options),
  );
}

// ===========================================================================
// DETAIL
// ===========================================================================

/**
 * GET /v1/tools/{id} — returns the row plus the "Used by" rollup
 * (tools-prd §4.2). Cross-audit §1.3 master ACL: non-readers see 404,
 * not 403, so existence is not leaked.
 */
export function fetchTool(
  identity: RequestIdentity,
  id: ToolId,
): Promise<ToolDetailResponse> {
  return httpGet<ToolDetailResponse>(toolPath(id), identity);
}

// ===========================================================================
// MUTATIONS — create / patch / delete / enable / disable
// ===========================================================================

/** POST /v1/tools — create a tool (tools-prd §4.3). */
export function createTool(
  identity: RequestIdentity,
  body: CreateToolRequest,
): Promise<Tool> {
  return httpPostQuery<Tool>(TOOLS_BASE, body, identity);
}

/**
 * PATCH /v1/tools/{id} — owner or tenant admin only (tools-prd §4.4).
 *
 * Server rejects bodies that include `usage` / `kind` / `owner_user_id` /
 * `tenant_id` with a 400; the wire shape pins this in `UpdateToolRequest`.
 */
export function patchTool(
  identity: RequestIdentity,
  id: ToolId,
  body: UpdateToolRequest,
): Promise<Tool> {
  return httpPatchQuery<Tool>(toolPath(id), body, identity);
}

/**
 * PATCH /v1/tools/{id} sugar — flip status to `disabled` (admin / owner
 * pause). Audit + grants are preserved (tools-prd §1.6 lifecycle).
 */
export function disableTool(
  identity: RequestIdentity,
  id: ToolId,
  reason?: string,
): Promise<Tool> {
  const body: UpdateToolRequest = { status: "disabled" };
  return patchTool(
    identity,
    id,
    reason !== undefined ? { ...body, status_reason: reason } : body,
  );
}

/** PATCH /v1/tools/{id} sugar — clear `disabled` / `error` back to enabled. */
export function enableTool(
  identity: RequestIdentity,
  id: ToolId,
): Promise<Tool> {
  return patchTool(identity, id, { status: "enabled" });
}

/**
 * DELETE /v1/tools/{id} — soft delete (tools-prd §4.4). Tombstone
 * retained per the destination's retention rules; cascade is owned by
 * the server. Returns void on success.
 */
export function deleteTool(
  identity: RequestIdentity,
  id: ToolId,
): Promise<void> {
  return httpDelete(toolPath(id), identity);
}

// ===========================================================================
// TEST CALL — POST /v1/tools/{id}/test
// ===========================================================================

/**
 * POST /v1/tools/{id}/test — sandbox executor runs the call with
 * `args` validated against `args_schema` (tools-prd §4.5). P10-A2
 * stubs the route as a 501; P10-A3 lands the live executor. The wire
 * shape is stable across both phases — surface the response either way.
 */
export function testToolCall(
  identity: RequestIdentity,
  id: ToolId,
  body: TestToolCallRequest,
): Promise<TestToolCallResponse> {
  return httpPostQuery<TestToolCallResponse>(
    `${toolPath(id)}/test`,
    body,
    identity,
  );
}

// ===========================================================================
// INVOCATIONS — audit lens (one row per call)
// ===========================================================================

export interface FetchInvocationsOptions {
  /** Opaque cursor from a previous response's `next_cursor`. */
  readonly after?: string;
  /** 1..200; server default 50. */
  readonly limit?: number;
  /** Filter by caller kind (chat / agent / routine). */
  readonly caller_kind?: "agent" | "routine" | "chat";
  /** Filter to only failed invocations. */
  readonly status?: "ok" | "error";
}

/**
 * GET /v1/tools/{id}/invocations — paginated invocation rows
 * (tools-prd §4.6). Source: `runtime_tool_invocations` projection —
 * TU-1 single-tracker invariant (cross-audit §5.5).
 */
export function fetchInvocations(
  identity: RequestIdentity,
  id: ToolId,
  options: FetchInvocationsOptions = {},
): Promise<ToolInvocationListResponse> {
  const params: Record<string, string | undefined> = {};
  if (options.after !== undefined) params.after = options.after;
  if (options.limit !== undefined) params.limit = String(options.limit);
  if (options.caller_kind !== undefined) {
    params["filter[caller_kind]"] = options.caller_kind;
  }
  if (options.status !== undefined) {
    params["filter[status]"] = options.status;
  }
  return httpGet<ToolInvocationListResponse>(
    `${toolPath(id)}/invocations`,
    identity,
    params,
  );
}

// ===========================================================================
// USAGE — read-only projection (tools-prd §3.3, §4.7)
// ===========================================================================

/**
 * GET /v1/tools/{id}/usage — 24h / 7d / 30d windowed projection over
 * `runtime_tool_invocations` + `runtime_model_call_usage`. Caller must
 * be able to read the tool (cross-audit §1.3 ACL).
 */
export function fetchUsage(
  identity: RequestIdentity,
  id: ToolId,
): Promise<ToolUsageResponse> {
  return httpGet<ToolUsageResponse>(`${toolPath(id)}/usage`, identity);
}

// ===========================================================================
// SSE — durable tools channel (tools-prd §4.10)
// ===========================================================================

/** Closeable handle for a running tool-events SSE subscription. */
export interface ToolStream {
  close(): void;
}

export interface OpenToolStreamOptions {
  readonly identity: RequestIdentity;
  /** Highest `sequence_no` already applied; backend replays everything strictly greater. */
  readonly afterSequence?: number;
  readonly onEvent: (envelope: ToolStreamEnvelope) => void;
  readonly onError: (err: Event) => void;
  readonly onOpen?: () => void;
}

/**
 * Open the durable tools-events SSE stream (tools-prd §4.10). Each
 * frame carries one `ToolStreamEnvelope`; the client tracks the highest
 * `sequence_no` and reconnects with `?after_sequence=N` to resume without
 * dropping events (cross-audit §5.2).
 *
 * Reconnect policy is owned caller-side (mirrors `streamAgentEvents` /
 * `streamRoutineEvents`) — the wrapper exposes one connection attempt
 * plus a stable error hook so tests can drive the timing
 * deterministically.
 */
export function openToolStream({
  identity,
  afterSequence,
  onEvent,
  onError,
  onOpen,
}: OpenToolStreamOptions): ToolStream {
  return getAppTransport().subscribeServerSentEvents({
    path: `${TOOLS_BASE}/stream`,
    query: toolSseQueryFor(identity, afterSequence),
    eventName: SSE_EVENT_NAME,
    onOpen,
    onError: (_err) => onError(streamErrorEvent()),
    onMessage: (data) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(data) as unknown;
      } catch {
        // Malformed JSON — drop the frame. Mirrors agentsApi / routinesApi
        // behavior: a single bad frame must not tear down the connection;
        // the caller has `onError` for the broader "stream broken" signal.
        return;
      }
      if (isToolStreamEnvelope(parsed)) {
        onEvent(parsed);
      }
    },
  });
}

// ===========================================================================
// Helpers
// ===========================================================================

function toolPath(id: ToolId): string {
  return `${TOOLS_BASE}/${encodeURIComponent(id)}`;
}

function encodeListParams(
  options: FetchToolsOptions,
): Record<string, string | undefined> {
  const params: Record<string, string | undefined> = {};
  const { filters, q, sort, after, limit } = options;

  if (filters?.kind !== undefined) params["filter[kind]"] = filters.kind;
  if (filters?.scope !== undefined) params["filter[scope]"] = filters.scope;
  if (filters?.status !== undefined) params["filter[status]"] = filters.status;
  if (filters?.owner_user_id !== undefined) {
    params["filter[owner_user_id]"] = filters.owner_user_id;
  }
  if (filters?.project_id !== undefined) {
    params["filter[project_id]"] = filters.project_id;
  }
  if (filters?.connector_id !== undefined) {
    params["filter[connector_id]"] = filters.connector_id;
  }
  if (filters?.tag !== undefined) params["filter[tag]"] = filters.tag;
  if (q !== undefined && q.length > 0) params.q = q;
  if (sort !== undefined) params.sort = sort;
  if (after !== undefined) params.after = after;
  if (limit !== undefined) params.limit = String(limit);
  return params;
}

function toolSseQueryFor(
  identity: RequestIdentity,
  afterSequence: number | undefined,
): Record<string, string> {
  const out: Record<string, string> = {
    org_id: identity.orgId,
    user_id: identity.userId,
  };
  if (afterSequence !== undefined) {
    out.after_sequence = String(afterSequence);
  }
  return out;
}

/**
 * Loose structural check on the SSE envelope. Matches the discriminator
 * fields from tools-prd §4.10 — `sequence_no` (number), `event_type`
 * (string), `event_id` (string), `created_at` (string). `tool` /
 * `invocation` are conditional per the union; we don't require either
 * here because `tool.heartbeat` carries neither.
 */
function isToolStreamEnvelope(value: unknown): value is ToolStreamEnvelope {
  if (value === null || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.event_id === "string" &&
    typeof v.sequence_no === "number" &&
    typeof v.event_type === "string" &&
    typeof v.created_at === "string"
  );
}

// The legacy onError signature was modelled after EventSource's bare
// Event — callers only react to "stream broken" and reconnect. Mirrors
// `streamAgentEvents` / `streamRoutineEvents`.
function streamErrorEvent(): Event {
  if (typeof Event === "function") {
    return new Event("error");
  }
  return { type: "error" } as unknown as Event;
}
