// Pure adapter functions mapping wire shapes (`@enterprise-search/api-types`)
// to presentation props for the Tools destination views.
//
// Pure: no React, no I/O, no module-level state. Every transform here is
// idempotent — call it twice with the same input, get the same output.
// Keeping the mapping in this file means the ToolsRoute / ToolDetailRoute
// / ToolOnboardingRoute components can be tested at the view layer with
// pre-shaped props rather than re-running the mapping in every test.

import type {
  Tool,
  ToolDetailResponse,
  ToolInvocation,
  ToolStreamEnvelope,
  ToolUsageProjection,
} from "@enterprise-search/api-types";

/**
 * One row in the Tools list. Pre-shaped so the destination row
 * component is a plain `(props) => JSX` — no further mapping at render.
 */
export interface ToolListRowProps {
  readonly id: string;
  readonly name: string;
  readonly description: string;
  readonly kind: Tool["kind"];
  readonly scope: Tool["scope"];
  readonly status: Tool["status"];
  /** When status is `error` / `disabled`, surfaces the reason inline. */
  readonly status_reason: string | null;
  readonly tags: ReadonlyArray<string>;
  /** Pre-formatted "Last used …" string; `null` when never used. */
  readonly last_used_label: string | null;
  readonly calls_30d: number;
  /** 0-100 (percent), null when no calls. */
  readonly success_pct_30d: number | null;
}

/**
 * Detail header presentation props. Read-only — the editor on the
 * detail view stages its own local draft state. `consumers` is shaped
 * as a single string per kind to keep the rollup pane cheap to render.
 */
export interface ToolDetailHeaderProps {
  readonly id: string;
  readonly name: string;
  readonly description: string;
  readonly kind: Tool["kind"];
  readonly scope: Tool["scope"];
  readonly status: Tool["status"];
  readonly status_reason: string | null;
  readonly transport_kind: Tool["transport"]["kind"];
  readonly transport_summary: string;
  readonly tags: ReadonlyArray<string>;
  readonly created_at: string;
  readonly updated_at: string;
  readonly consumer_summary: {
    readonly agents_count: number;
    readonly routines_count: number;
    readonly chats_with_grant: number;
  };
}

/**
 * One invocation row in the audit-lens pane. Truncates summaries to
 * 120 chars at the presentation layer (the wire already truncates to
 * 240) — keeps the row dense.
 */
export interface InvocationRowProps {
  readonly id: string;
  readonly status: "ok" | "error";
  readonly error_kind: ToolInvocation["error_kind"] | null;
  readonly caller_kind: ToolInvocation["caller_kind"];
  readonly args_preview: string;
  readonly result_preview: string | null;
  readonly latency_label: string;
  readonly started_at: string;
}

// ===========================================================================
// Adapters
// ===========================================================================

/**
 * Wire `Tool` → list-row presentation props. Pure.
 */
export function toolToListRow(tool: Tool): ToolListRowProps {
  return {
    id: tool.id,
    name: tool.name,
    description: tool.description,
    kind: tool.kind,
    scope: tool.scope,
    status: tool.status,
    status_reason: tool.status_reason ?? null,
    tags: tool.tags,
    last_used_label: formatLastUsed(tool.usage.last_used_at),
    calls_30d: tool.usage.calls_30d,
    success_pct_30d: percentOrNull(tool.usage.success_rate_30d),
  };
}

/**
 * Wire `ToolDetailResponse` → detail-header presentation props. Pure.
 */
export function detailToHeaderProps(
  res: ToolDetailResponse,
): ToolDetailHeaderProps {
  const t = res.tool;
  return {
    id: t.id,
    name: t.name,
    description: t.description,
    kind: t.kind,
    scope: t.scope,
    status: t.status,
    status_reason: t.status_reason ?? null,
    transport_kind: t.transport.kind,
    transport_summary: summarizeTransport(t),
    tags: t.tags,
    created_at: t.created_at,
    updated_at: t.updated_at,
    consumer_summary: {
      agents_count: res.consumers.agents.length,
      routines_count: res.consumers.routines.length,
      chats_with_grant: res.consumers.chats_with_grant,
    },
  };
}

/** Wire `ToolInvocation` → row presentation props. Pure. */
export function invocationToRow(inv: ToolInvocation): InvocationRowProps {
  return {
    id: inv.id,
    status: inv.status,
    error_kind: inv.error_kind ?? null,
    caller_kind: inv.caller_kind,
    args_preview: truncate(inv.args_summary, 120),
    result_preview:
      inv.result_summary !== undefined
        ? truncate(inv.result_summary, 120)
        : null,
    latency_label: `${inv.latency_ms}ms`,
    started_at: inv.started_at,
  };
}

/**
 * Apply one durable SSE envelope to a tools list. Pure.
 *
 * Semantics (tools-prd §4.10 event types):
 * - `tool.created`         → prepend the row (when present).
 * - `tool.updated`         → replace in place (when found).
 * - `tool.error_threshold` → replace in place (status flip is on `tool`).
 * - `tool.deleted`         → drop the row.
 * - `tool.invoked`         → no-op at list layer; caller can re-fetch
 *                            usage projection for the affected row.
 * - `tool.heartbeat`       → no-op.
 *
 * The pure shape lets a test drive the reducer without a mounted
 * component, matching `applyAgentEnvelope` from AgentsRoute.
 */
export function applyToolEnvelope(
  items: ReadonlyArray<Tool>,
  envelope: ToolStreamEnvelope,
): ReadonlyArray<Tool> {
  switch (envelope.event_type) {
    case "tool.created": {
      if (envelope.tool === undefined) return items;
      // Idempotent — replace if id already present, else prepend.
      const idx = items.findIndex((t) => t.id === envelope.tool!.id);
      if (idx !== -1) {
        const next = items.slice();
        next[idx] = envelope.tool;
        return next;
      }
      return [envelope.tool, ...items];
    }
    case "tool.updated":
    case "tool.error_threshold": {
      if (envelope.tool === undefined) return items;
      const idx = items.findIndex((t) => t.id === envelope.tool!.id);
      if (idx === -1) return items;
      const next = items.slice();
      next[idx] = envelope.tool;
      return next;
    }
    case "tool.deleted": {
      if (envelope.tool === undefined) return items;
      const deletedId = envelope.tool.id;
      return items.filter((t) => t.id !== deletedId);
    }
    case "tool.invoked":
    case "tool.heartbeat":
    default:
      return items;
  }
}

// ===========================================================================
// Internal formatters
// ===========================================================================

/**
 * Format an ISO timestamp into a relative "Last used" label. Returns
 * `null` when the input is null. Pure — uses `Date.now()` at call time;
 * for testability, callers can pin time via vitest's fake timers.
 */
export function formatLastUsed(iso: string | null): string | null {
  if (iso === null) return null;
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return null;
  const diffMs = Date.now() - parsed;
  if (diffMs < 60_000) return "just now";
  if (diffMs < 3_600_000) return `${Math.floor(diffMs / 60_000)}m ago`;
  if (diffMs < 86_400_000) return `${Math.floor(diffMs / 3_600_000)}h ago`;
  const days = Math.floor(diffMs / 86_400_000);
  if (days < 30) return `${days}d ago`;
  return iso.slice(0, 10);
}

/**
 * Convert the wire success rate (0..1 or null) into a 0..100 percent
 * integer, rounded to the nearest 1%. Pure.
 */
export function percentOrNull(
  rate: ToolUsageProjection["success_rate_30d"],
): number | null {
  if (rate === null) return null;
  return Math.round(rate * 100);
}

/**
 * Short, human-readable transport summary. Used in the detail header so
 * the user can see "MCP server: slack" / "HTTP: https://api…" without
 * expanding a full block. Pure.
 */
export function summarizeTransport(tool: Tool): string {
  const t = tool.transport;
  switch (t.kind) {
    case "mcp":
      return t.connector_ref
        ? `MCP via connector ${t.connector_ref.id}`
        : "MCP";
    case "http":
      return t.url_template ? `HTTP: ${t.url_template}` : "HTTP";
    case "in_process":
      return t.executor ? `Built-in: ${t.executor}` : "Built-in";
    case "sandbox":
      return t.executor ? `Sandbox: ${t.executor}` : "Sandbox";
    default: {
      // Exhaustiveness guard — adapter must extend if a new transport
      // kind lands on the wire shape.
      return "Unknown transport";
    }
  }
}

function truncate(s: string, max: number): string {
  if (s.length <= max) return s;
  return s.slice(0, max - 1) + "…";
}
