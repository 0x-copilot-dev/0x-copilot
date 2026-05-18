// Tools destination — canonical wire shape (Phase 10 P10-A1).
//
// Source: docs/atlas-new-design/destinations/tools-prd.md §3.1.
// Authority: this is the SINGLE declaration site for every Tools-
// destination payload type. Re-exported from `packages/api-types/src/index.ts`;
// every consumer (frontend, chat-surface, backend, ai-backend) imports
// from `@enterprise-search/api-types` — drift here is a bug.
//
// Five concerns this file owns:
//
//   1. The `Tool` row shape — args/returns JSON Schemas, transport, owner,
//      project, optional skill/code refs, the read-only usage projection.
//   2. The `ToolKind` / `ToolScope` / `ToolStatus` enumerations.
//   3. The `ToolTransport` discriminated branch (`mcp` / `http` /
//      `in_process` / `sandbox`).
//   4. The `ToolInvocation` row — one per call; the audit lens for "who
//      called what". This is a PROJECTION over `runtime_tool_invocations`
//      (existing table); not a parallel tracker (cross-audit §5.5 TU-1).
//   5. The `ToolUsageProjection` — likewise a read-time GROUP BY over the
//      same table + `runtime_model_call_usage`.
//
// The destination is the catalog + onboarding + audit lens (tools-prd §1.1):
// chats, agents, routines, and code-routines all converge on the same
// `Tool` row. One catalog, four kinds.

import type {
  ConnectorId,
  LibraryPageId,
  ProjectId,
  RunId,
  TenantId,
  ToolId,
  UserId,
} from "./brands";
import type { ItemRef } from "./refs";

/**
 * Tool kind discriminator (tools-prd §3.1).
 *
 * - `mcp` — a method on a registered MCP server.
 * - `openapi` — an operation from an onboarded OpenAPI document.
 * - `builtin` — Atlas-shipped capability (file read/write, web search, …).
 * - `code` — user-authored deterministic code-routine (forwards-compat
 *   slot for Routines §9.7 Q1; sandbox executor lands in P10-A3).
 * - `skill` — first-class Library page tagged `skill`; the prompt lives
 *   in Library, the wire-callable shim lives here.
 */
export type ToolKind = "mcp" | "openapi" | "builtin" | "code" | "skill";

/**
 * Effective IO scope. Drives default per-chat / per-agent allowlists
 * (read-only contexts can grant `read` and `both` but never the bare
 * `write` flavour). `both` is "issues mutations and reads results back".
 */
export type ToolScope = "read" | "write" | "both";

/**
 * Lifecycle status (tools-prd §1.6).
 *
 * - `enabled` — installed, scope reviewed, callable by every grant.
 * - `disabled` — admin/owner paused; grants + audit preserved.
 * - `error` — auto-set after consecutive transport failures (auth expired,
 *   sandbox crash, schema mismatch). Auto-cleared on first success.
 * - `pending_review` — code-routines and OpenAPI scope-change rebuilds
 *   sit here until an admin approves.
 */
export type ToolStatus = "enabled" | "disabled" | "error" | "pending_review";

/**
 * Wire kinds of `ToolTransport.kind`. Reused on the storage row as the
 * JSONB `transport.kind` discriminator.
 */
export type ToolTransportKind = "mcp" | "http" | "in_process" | "sandbox";

/**
 * How a tool call is dispatched at runtime. Each tool has exactly one
 * transport.
 *
 * - `mcp` — dispatched via the registered MCP server's tool method.
 * - `http` — issues an HTTP request via `url_template`; auth resolved
 *   through `connector_ref` (if set) or an inline API key in the vault.
 * - `in_process` — handled by a built-in registered in the runtime via
 *   the named `executor` (e.g. `web_search`, `library_save`).
 * - `sandbox` — runs in the code-routine sandbox; `executor` is the
 *   sandbox-resolver name (P10-A3 lands the executor).
 */
export interface ToolTransport {
  readonly kind: ToolTransportKind;
  /** When `kind=http`: URL template (vars substituted at call time). */
  readonly url_template?: string;
  /**
   * Reverse-link to the connector that authenticates this tool, if any.
   * Lets Phase 11 Connectors render "tools that use me" in O(1).
   */
  readonly connector_ref?: {
    readonly kind: "connector";
    readonly id: ConnectorId;
  };
  /** For `sandbox` / `in_process`: name of the resolved executor. */
  readonly executor?: string;
}

/**
 * Read-time GROUP BY over `runtime_tool_invocations` (one row per call)
 * and `runtime_model_call_usage` (when a tool wraps an LLM step). NEVER
 * a parallel tracker — TU-1 single-tracker invariant (cross-audit §5.5)
 * is preserved.
 */
export interface ToolUsageProjection {
  readonly calls_24h: number;
  readonly calls_30d: number;
  /** Median latency in ms over the 30-day window; null when no calls. */
  readonly p50_latency_ms_30d: number | null;
  /** 0-1; null when no calls. */
  readonly success_rate_30d: number | null;
  readonly last_used_at: string | null;
}

/**
 * Code-routine forward-compatibility ref (Routines §9.7 Q1). Mirrors the
 * `RoutineCode` wire shape from `./routines.ts` so a routine's
 * `actions[].tool_call { tool_id }` can resolve to a `kind = "code"`
 * tool without a parallel "code-routine" wire path.
 */
export interface ToolCodeRef {
  readonly repo_ref: ItemRef;
  readonly env_ref: ItemRef;
  /** Function entry point — e.g. `module.main` or `pkg.module:run`. */
  readonly entry: string;
}

/**
 * Skill-page back-link (when `kind = "skill"`). The prompt + response
 * template live on the referenced Library page; the Tool row carries
 * only the wire-callable shim.
 */
export interface ToolSkillPageRef {
  readonly kind: "library_page";
  readonly id: LibraryPageId;
}

/**
 * Canonical Tool row (tools-prd §3.1). Returned from `GET /v1/tools`,
 * `GET /v1/tools/{id}`, embedded in SSE envelopes.
 *
 * Authorization summary (tools-prd §6):
 *
 * - When `project_id` is set, the master ACL rule from cross-audit §1.3
 *   applies: non-readers see 404 (existence not leaked); only project
 *   members + the owner + admins read.
 * - Writes are owner OR tenant admin only.
 * - `usage` is server-computed and read-only at the wire boundary —
 *   PATCH bodies that include it are rejected (400).
 */
export interface Tool {
  readonly id: ToolId;
  readonly tenant_id: TenantId;
  readonly name: string;
  readonly description: string;
  readonly kind: ToolKind;
  readonly scope: ToolScope;
  readonly status: ToolStatus;
  readonly status_reason?: string;
  /** JSON Schemas (Draft 2020-12) — server-validated at call time. */
  readonly args_schema: Record<string, unknown>;
  readonly returns_schema: Record<string, unknown>;
  readonly transport: ToolTransport;
  /**
   * Owner is the user who registered/authored the tool. Tenant admins
   * can edit any tool; project members can only call them via grants.
   */
  readonly owner_user_id: UserId;
  /**
   * Project this tool was filed under (optional). When non-null the
   * cross-audit §1.3 master rule applies: non-readers 404; catalog
   * list filters by visibility.
   */
  readonly project_id?: ProjectId | null;
  /** Back-link for `kind = "skill"`. */
  readonly skill_page_ref?: ToolSkillPageRef;
  /** Forward-compat shape for `kind = "code"` (Routines §9.7 Q1). */
  readonly code_ref?: ToolCodeRef;
  readonly tags: ReadonlyArray<string>;
  /** Read-only projection — §3.3. */
  readonly usage: ToolUsageProjection;
  readonly created_at: string;
  readonly updated_at: string;
}

/**
 * Reasons a tool invocation can fail at the transport layer. Auto-
 * classified by the runtime; drives Inbox routing for owner-actionable
 * errors (`auth_required` / `scope_missing` ride the `tool_error` inbox
 * kind per tools-prd §1.5).
 */
export type ToolInvocationErrorKind =
  | "auth_required"
  | "scope_missing"
  | "schema_invalid"
  | "timeout"
  | "sandbox_crash"
  | "transport_error"
  | "unknown";

/**
 * Caller-kind discriminator for an invocation. Lets the audit / detail
 * view filter "by callsite" without joining four upstream tables.
 */
export type ToolInvocationCallerKind = "agent" | "routine" | "chat";

/**
 * One row in `runtime_tool_invocations`. The full args/result payload
 * lives in the audit trail (redacted per tenant config); the wire
 * shape carries `*_summary` truncated to 240 chars so the invocation
 * list is cheap to render.
 */
export interface ToolInvocation {
  /** `toolinv_<ulid>` (server-issued). */
  readonly id: string;
  readonly tool_id: ToolId;
  readonly tenant_id: TenantId;
  readonly run_id: RunId;
  readonly caller_kind: ToolInvocationCallerKind;
  /** Narrowed to `agent` / `routine` / `chat` on the wire. */
  readonly caller_ref: ItemRef;
  /** Truncated to 240 chars; full payload in audit. */
  readonly args_summary: string;
  /** Truncated; absent when the invocation errored. */
  readonly result_summary?: string;
  readonly status: "ok" | "error";
  readonly error_kind?: ToolInvocationErrorKind;
  readonly started_at: string;
  readonly ended_at: string;
  readonly latency_ms: number;
}

/**
 * `GET /v1/tools` response — cursor-paginated.
 */
export interface ToolListResponse {
  readonly tools: ReadonlyArray<Tool>;
  readonly next_cursor: string | null;
}

/**
 * `GET /v1/tools/{id}` response — Tool plus the "Used by" rollup.
 *
 * `chats_with_grant` is a count (not a list) because per-chat grants
 * are admin-only data; the detail view should not leak chat ids to
 * non-admin consumers.
 */
export interface ToolDetailResponse {
  readonly tool: Tool;
  readonly consumers: {
    /** Narrowed to `kind: "agent"`. */
    readonly agents: ReadonlyArray<ItemRef>;
    /** Narrowed to `kind: "routine"`. */
    readonly routines: ReadonlyArray<ItemRef>;
    readonly chats_with_grant: number;
  };
}

/**
 * `POST /v1/tools` request body (tools-prd §4.3).
 *
 * Note: when `kind = "code"` the server validates the sandbox build step
 * before persisting; the route may return `202 Accepted` + a build job id
 * when the build is async. P10-A3 lands the build pipeline; P10-A2 stubs
 * the build call to "not yet wired" and short-circuits.
 */
export interface CreateToolRequest {
  readonly kind: ToolKind;
  readonly name: string;
  readonly description: string;
  readonly scope: ToolScope;
  readonly args_schema: Record<string, unknown>;
  readonly returns_schema: Record<string, unknown>;
  readonly transport: ToolTransport;
  readonly project_id?: ProjectId | null;
  readonly tags?: ReadonlyArray<string>;
  readonly skill_page_ref?: ToolSkillPageRef;
  readonly code_ref?: ToolCodeRef;
}

/**
 * `PATCH /v1/tools/{id}` request body (tools-prd §4.4).
 *
 * Patchable: name, description, tags, scope (down-shrink only without
 * review), status (enable/disable), args_schema (only for `kind=code`).
 *
 * `usage`, `kind`, `owner_user_id`, `tenant_id` are NOT patchable —
 * include them in a PATCH body and the server rejects with 400.
 */
export interface UpdateToolRequest {
  readonly name?: string;
  readonly description?: string;
  readonly scope?: ToolScope;
  readonly status?: ToolStatus;
  readonly status_reason?: string;
  readonly tags?: ReadonlyArray<string>;
  readonly args_schema?: Record<string, unknown>;
  readonly returns_schema?: Record<string, unknown>;
  readonly transport?: ToolTransport;
  readonly project_id?: ProjectId | null;
}

/**
 * `POST /v1/tools/{id}/test` request body.
 *
 * The args validate against `args_schema` before dispatch; the result is
 * audit-logged via the existing `tool.test_called` action. P10-A2 ships
 * the route as a 501 stub; the sandbox executor that actually runs the
 * call lands in P10-A3.
 */
export interface TestToolCallRequest {
  readonly args: Record<string, unknown>;
}

/**
 * `POST /v1/tools/{id}/test` response.
 *
 * When the executor isn't wired yet (P10-A2 → P10-A3 handoff) the route
 * returns 501; this shape is the success contract once P10-A3 lands.
 */
export interface TestToolCallResponse {
  readonly status: "ok" | "error";
  readonly result?: unknown;
  readonly latency_ms: number;
  readonly error?: {
    readonly kind: ToolInvocationErrorKind;
    readonly message: string;
  };
}

/**
 * `GET /v1/tools/{id}/invocations` response — paginated.
 */
export interface ToolInvocationListResponse {
  readonly invocations: ReadonlyArray<ToolInvocation>;
  readonly next_cursor: string | null;
}

/**
 * `GET /v1/tools/{id}/usage` response — projection over the existing
 * `runtime_tool_invocations` + `runtime_model_call_usage` tables.
 * 24h / 7d / 30d windows plus the rolled-up shape that matches the
 * `Tool.usage` field on the row.
 */
export interface ToolUsageResponse {
  readonly tool_id: ToolId;
  readonly windows: {
    readonly window_24h: ToolUsageProjection;
    readonly window_7d: ToolUsageProjection;
    readonly window_30d: ToolUsageProjection;
  };
}

/**
 * SSE event types on `GET /v1/tools/stream` (tools-prd §4.10).
 *
 * - `tool.created` — new tool registered.
 * - `tool.updated` — patch landed (status / scope / name / tags / …).
 * - `tool.deleted` — soft-delete.
 * - `tool.invoked` — a new invocation landed (server batches at ~1Hz).
 * - `tool.error_threshold` — `status` flipped to `error`.
 * - `tool.heartbeat` — every 30s keepalive (cross-audit §5.2).
 */
export type ToolStreamEventType =
  | "tool.created"
  | "tool.updated"
  | "tool.deleted"
  | "tool.invoked"
  | "tool.error_threshold"
  | "tool.heartbeat";

/**
 * SSE envelope for `GET /v1/tools/stream`. Mirrors the inbox / home /
 * project / routine stream envelope shapes — monotonic `sequence_no`
 * per `(org_id, user_id)` channel, browsers replay via `Last-Event-ID`.
 *
 * `tool` is present for `tool.created` / `tool.updated` / `tool.deleted`
 * / `tool.error_threshold`. `invocation` is present for `tool.invoked`.
 * Both are absent for `tool.heartbeat`.
 */
export interface ToolStreamEnvelope {
  readonly event_id: string;
  readonly sequence_no: number;
  readonly event_type: ToolStreamEventType;
  readonly tool?: Tool;
  readonly invocation?: ToolInvocation;
  readonly created_at: string;
}
