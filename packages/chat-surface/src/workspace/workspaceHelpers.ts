// PR-1.7 — substrate-portable copies of the pure helpers the hoisted
// workspace pane + tabs depend on.
//
// The workspace pane presentation family was hoisted from apps/frontend into
// chat-surface (FR-1.24) so web and desktop render the right rail identically.
// chat-surface MUST NOT import apps/frontend (eslint no-restricted-imports),
// and FR-1.25 keeps the host as the owner of the `chatModel/*` reducers
// (`sourcesReducer`, `subagentReducer`, `subagentStatus`) — those reducers
// project the live SSE stream for the whole web app (event reducing, seeding,
// `citedToolSources`), not just these tabs, so they stay host-owned.
//
// The small, pure derivation helpers the moved tabs call are therefore
// reproduced here byte-for-byte. They are pure functions of their
// `@0x-copilot/api-types` inputs (no DOM, no globals), so the two copies
// render identically; unifying them onto a single `@0x-copilot/api-types`
// home is a later reconciliation (the same deferral the PRD applies to
// `depth.ts` and `subagentHelpers.ts`). Provenance of each helper is noted
// inline.

import {
  isCitationMadePayload,
  isSourceIngestedPayload,
  isSourcesIngestedPayload,
  isToolCallPayload,
  isToolResultPayload,
  type CitationSourceRef,
  type RuntimeEventEnvelope,
  type SourceEntry,
  type SubagentEntry,
  type SubagentLifecycleStatus,
} from "@0x-copilot/api-types";

// ── from apps/frontend/.../chatModel/sourcesReducer.ts ───────────────────

/** One row per unique `(source_connector, source_doc_id)` pair. */
export type SourceEntryMap = ReadonlyMap<string, SourceEntry>;

/** Rows ordered by citation_count desc, then last_cited_at desc. */
export function sourcesByCitationCount(
  current: SourceEntryMap,
): readonly SourceEntry[] {
  return [...current.values()].sort((a, b) => {
    if (b.citation_count !== a.citation_count) {
      return b.citation_count - a.citation_count;
    }
    return Date.parse(b.last_cited_at) - Date.parse(a.last_cited_at);
  });
}

export interface SourceConnectorGroup {
  readonly connector: string;
  readonly total: number;
  readonly rows: readonly SourceEntry[];
}

/**
 * Group rows by connector for the Sources tab when the list gets long
 * enough that flat scanning is slow. Sections are sorted by total
 * citation count desc, then alphabetically by connector. Within a section,
 * the row order from {@link sourcesByCitationCount} is preserved.
 */
export function groupSourcesByConnector(
  ordered: readonly SourceEntry[],
): readonly SourceConnectorGroup[] {
  const groups = new Map<string, SourceEntry[]>();
  for (const source of ordered) {
    const bucket = groups.get(source.source_connector);
    if (bucket) {
      bucket.push(source);
    } else {
      groups.set(source.source_connector, [source]);
    }
  }
  const out: SourceConnectorGroup[] = [];
  for (const [connector, rows] of groups.entries()) {
    const total = rows.reduce((sum, row) => sum + row.citation_count, 0);
    out.push({ connector, total, rows });
  }
  out.sort((a, b) => {
    if (b.total !== a.total) {
      return b.total - a.total;
    }
    return a.connector.localeCompare(b.connector);
  });
  return out;
}

// The Sources REDUCER (seed + live event fold) was previously host-only
// (FR-1.25) because it projected the whole web app's stream. The Run cockpit
// now projects the SAME single stream package-side (like projectSubagents /
// projectApprovals / useRunTranscript), so the reducer is reproduced here
// byte-for-byte and consumed by `useRunSources`.

/** Empty conversation source map. */
export function emptySourceMap(): SourceEntryMap {
  return new Map();
}

/** Seed from the `GET .../sources` response — one row per unique doc. */
export function seedSourceMap(entries: readonly SourceEntry[]): SourceEntryMap {
  return new Map(entries.map((entry) => [keyFor(entry), entry]));
}

/**
 * Fold one runtime event into the source map: `source_ingested` (one citation)
 * and batched `sources_ingested` (many) merge each `CitationSourceRef` on
 * `(source_connector, source_doc_id)`, bumping `citation_count`. Any other
 * event returns the map unchanged (referential stability preserved on replay).
 */
export function applySourceEvent(
  current: SourceEntryMap,
  event: RuntimeEventEnvelope,
): SourceEntryMap {
  if (event.event_type === "source_ingested") {
    if (!isSourceIngestedPayload(event.payload)) {
      return current;
    }
    return mergeOne(current, event.payload.citation, event.created_at);
  }
  if (event.event_type === "sources_ingested") {
    if (!isSourcesIngestedPayload(event.payload)) {
      return current;
    }
    if (event.payload.citations.length === 0) {
      return current;
    }
    let next = current;
    for (const citation of event.payload.citations) {
      next = mergeOne(next, citation, event.created_at);
    }
    return next;
  }
  return current;
}

function mergeOne(
  current: SourceEntryMap,
  citation: CitationSourceRef,
  eventCreatedAt: string,
): SourceEntryMap {
  const key = keyForCitation(citation.source_connector, citation.source_doc_id);
  const existing = current.get(key);
  const next = mergeIncoming({
    existing,
    citation_id: citation.citation_id,
    source_connector: citation.source_connector,
    source_doc_id: citation.source_doc_id,
    source_url: citation.source_url ?? null,
    title: citation.title ?? null,
    snippet: citation.snippet ?? null,
    freshness_at: citation.freshness_at ?? null,
    eventCreatedAt,
  });
  if (next === existing) {
    return current;
  }
  const out = new Map(current);
  out.set(key, next);
  return out;
}

function keyFor(entry: SourceEntry): string {
  return keyForCitation(entry.source_connector, entry.source_doc_id);
}

function keyForCitation(connector: string, docId: string): string {
  return `${connector} ${docId}`;
}

function mergeIncoming(opts: {
  existing: SourceEntry | undefined;
  citation_id: string;
  source_connector: string;
  source_doc_id: string;
  source_url: string | null;
  title: string | null;
  snippet: string | null;
  freshness_at: string | null;
  eventCreatedAt: string;
}): SourceEntry {
  if (opts.existing === undefined) {
    return {
      citation_id: opts.citation_id,
      source_connector: opts.source_connector,
      source_doc_id: opts.source_doc_id,
      source_url: opts.source_url,
      title: opts.title,
      snippet: opts.snippet,
      freshness_at: opts.freshness_at,
      citation_count: 1,
      last_cited_at: opts.eventCreatedAt,
    };
  }
  const eventTs = Date.parse(opts.eventCreatedAt);
  const existingTs = Date.parse(opts.existing.last_cited_at);
  const isNewer = !Number.isNaN(eventTs) && eventTs >= existingTs;
  return {
    citation_id: isNewer ? opts.citation_id : opts.existing.citation_id,
    source_connector: opts.existing.source_connector,
    source_doc_id: opts.existing.source_doc_id,
    source_url: opts.source_url ?? opts.existing.source_url,
    title: opts.title ?? opts.existing.title,
    snippet: opts.snippet ?? opts.existing.snippet,
    freshness_at: latestIso(opts.freshness_at, opts.existing.freshness_at),
    citation_count: opts.existing.citation_count + 1,
    last_cited_at: isNewer ? opts.eventCreatedAt : opts.existing.last_cited_at,
  };
}

function latestIso(a: string | null, b: string | null): string | null {
  if (a === null) {
    return b;
  }
  if (b === null) {
    return a;
  }
  return Date.parse(a) >= Date.parse(b) ? a : b;
}

// ── Cited-tool sources (WC-P6c) ───────────────────────────────────────────
//
// `citation_made` carries a `CitationLink` (an ordinal → `source_tool_call_id`
// pointer), NOT a `CitationSourceRef` — so, unlike `source_ingested`, it cannot
// fold per-event through `applySourceEvent`. The cited row's connector / title /
// snippet live on the cited tool invocation (a `tool_call` + `tool_result` earlier
// in the SAME run stream). This whole-events fold reproduces the host-owned
// `chatModel/citedToolSources` projection over raw events (the same byte-for-byte
// reproduction discipline this file already applies) so the cockpit's Sources tab
// surfaces cited tool calls the CitationProjector did NOT recognise as sources —
// the common MCP / DuckDuckGo case, where no `source_ingested` fires — matching
// the legacy web chat. Rows already present (a richer `source_ingested`
// `CitationSourceRef`) win on key collision. The synthetic `tool:` / `tool-call:`
// id + doc prefixes keep the two paths from key-colliding.

/** Synthetic citation-id prefix — keeps tool-call rows off legacy row keys. */
export const TOOL_CITATION_ID_PREFIX = "tool:";
/** Synthetic source_doc_id prefix — keeps the dedup key stable. */
export const TOOL_DOC_ID_PREFIX = "tool-call:";
const CITED_TOOL_SNIPPET_MAX = 280;

/** Compact view of a tool invocation, coalesced from its stream events. */
interface ToolCallSnapshot {
  readonly tool_name: string;
  readonly args: Record<string, unknown> | null;
  readonly result: string | null;
  readonly result_is_error: boolean;
}

const TOOL_ERROR_STATUSES: ReadonlySet<string> = new Set([
  "failed",
  "timed_out",
  "abandoned",
  "cancelled",
]);

/** Index the run's `tool_call` / `tool_result` events by `call_id`. First
 *  `tool_call` wins the name/args; the `tool_result` supplies the snippet + error
 *  flag. Mirrors the host `toolInvocationIndex`, sourced from raw events. */
function toolIndexFromEvents(
  events: readonly RuntimeEventEnvelope[],
): Map<string, ToolCallSnapshot> {
  const index = new Map<string, ToolCallSnapshot>();
  for (const event of events) {
    if (event.event_type === "tool_call") {
      if (!isToolCallPayload(event.payload)) {
        continue;
      }
      const callId = event.payload.call_id;
      if (index.has(callId)) {
        continue;
      }
      index.set(callId, {
        tool_name: event.payload.tool_name,
        args: isRecord(event.payload.args) ? event.payload.args : null,
        result: null,
        result_is_error: false,
      });
      continue;
    }
    if (event.event_type === "tool_result") {
      if (!isToolResultPayload(event.payload)) {
        continue;
      }
      const callId = event.payload.call_id;
      const status =
        typeof event.payload.status === "string" ? event.payload.status : "";
      const isError =
        TOOL_ERROR_STATUSES.has(status) ||
        typeof event.payload.error_message === "string";
      const result = toolResultText(event.payload);
      const existing = index.get(callId);
      index.set(callId, {
        tool_name:
          existing?.tool_name ?? event.payload.tool_name ?? "tool call",
        args: existing?.args ?? null,
        result,
        result_is_error: isError,
      });
    }
  }
  return index;
}

/** The human-readable result text for a tool_result — the backend's own summary
 *  first, then a safe message, then a compact stringification of the output. */
function toolResultText(payload: Record<string, unknown>): string | null {
  const summary =
    stringValue(payload.summary) ?? stringValue(payload.safe_message);
  if (summary !== null) {
    return summary;
  }
  const output = payload.output;
  if (isRecord(output) && Object.keys(output).length > 0) {
    try {
      return JSON.stringify(output);
    } catch {
      return null;
    }
  }
  return null;
}

/**
 * Union cited-tool-call rows into `base`. Buckets `citation_made` links by their
 * `source_tool_call_id` (count = number of chips pointing at the call), projects
 * each into a synthetic `SourceEntry` from the tool index, and merges — a row
 * already keyed in `base` (a richer `source_ingested` row) wins. Returns `base`
 * unchanged (referential stability) when there is nothing to add.
 */
export function foldCitedToolSources(
  base: SourceEntryMap,
  events: readonly RuntimeEventEnvelope[],
): SourceEntryMap {
  const counts = new Map<string, number>();
  for (const event of events) {
    if (event.event_type !== "citation_made") {
      continue;
    }
    if (!isCitationMadePayload(event.payload)) {
      continue;
    }
    const callId = event.payload.link.source_tool_call_id;
    // Empty `source_tool_call_id` is a hallucinated ordinal — the chip renders
    // `?` and the projection skips it rather than inventing a row.
    if (typeof callId !== "string" || callId === "") {
      continue;
    }
    counts.set(callId, (counts.get(callId) ?? 0) + 1);
  }
  if (counts.size === 0) {
    return base;
  }
  const toolIndex = toolIndexFromEvents(events);
  let out: Map<string, SourceEntry> | null = null;
  for (const [callId, count] of counts) {
    const entry = toCitedToolSource(callId, count, toolIndex.get(callId));
    const key = keyForCitation(entry.source_connector, entry.source_doc_id);
    if (base.has(key)) {
      continue;
    }
    if (out === null) {
      out = new Map(base);
    }
    out.set(key, entry);
  }
  return out ?? base;
}

function toCitedToolSource(
  callId: string,
  count: number,
  snapshot: ToolCallSnapshot | undefined,
): SourceEntry {
  const toolName = snapshot?.tool_name ?? "tool call";
  // MCP wrapper calls expose themselves as `call_tool` with the real server/tool
  // in args — derive the connector from `server_name` so the row groups under
  // e.g. `linear`, not `mcp`.
  let connector = connectorFromToolName(toolName);
  if (toolName === "call_tool" && snapshot?.args) {
    const serverName = stringValue(snapshot.args.server_name);
    if (serverName !== null) {
      connector = serverName.toLowerCase();
    }
  }
  return {
    citation_id: `${TOOL_CITATION_ID_PREFIX}${callId}`,
    source_connector: connector,
    source_doc_id: `${TOOL_DOC_ID_PREFIX}${callId}`,
    source_url: null,
    title: citedToolTitle(toolName, snapshot),
    snippet: citedToolSnippet(snapshot),
    freshness_at: null,
    citation_count: count,
    last_cited_at: "",
  };
}

/** Derive a connector slug from a tool name (MCP `<server>.<tool>` → `<server>`;
 *  `web_search` → `web`; control-plane `load_`/`call_` → `mcp`; else `tool`). */
function connectorFromToolName(toolName: string): string {
  const dot = toolName.indexOf(".");
  if (dot > 0) {
    return toolName.slice(0, dot).toLowerCase();
  }
  if (toolName === "web_search") {
    return "web";
  }
  if (toolName.startsWith("load_") || toolName.startsWith("call_")) {
    return "mcp";
  }
  return "tool";
}

function citedToolTitle(
  toolName: string,
  snapshot: ToolCallSnapshot | undefined,
): string {
  if (toolName === "call_tool" && snapshot?.args) {
    const serverName = stringValue(snapshot.args.server_name);
    const innerToolName = stringValue(snapshot.args.tool_name);
    if (serverName !== null && innerToolName !== null) {
      const inner = isRecord(snapshot.args.arguments)
        ? summarizeArgs(snapshot.args.arguments)
        : null;
      const head = `${serverName}.${innerToolName}`;
      return inner === null ? head : `${head} — ${inner}`;
    }
  }
  if (snapshot?.args) {
    const argsSummary = summarizeArgs(snapshot.args);
    if (argsSummary !== null) {
      return `${toolName} — ${argsSummary}`;
    }
  }
  return toolName;
}

function citedToolSnippet(
  snapshot: ToolCallSnapshot | undefined,
): string | null {
  if (snapshot === undefined) {
    return null;
  }
  if (snapshot.result_is_error) {
    return "(tool call failed)";
  }
  if (snapshot.result === null || snapshot.result.length === 0) {
    return null;
  }
  const trimmed = snapshot.result.trim();
  if (trimmed.length <= CITED_TOOL_SNIPPET_MAX) {
    return trimmed;
  }
  return `${trimmed.slice(0, CITED_TOOL_SNIPPET_MAX).trimEnd()}…`;
}

function summarizeArgs(args: Record<string, unknown>): string | null {
  for (const key of [
    "query",
    "q",
    "search",
    "name",
    "title",
    "subject",
    "tool_name",
  ]) {
    const value = args[key];
    if (typeof value === "string" && value.trim().length > 0) {
      const trimmed = value.trim();
      return trimmed.length > 64
        ? `${trimmed.slice(0, 64).trimEnd()}…`
        : trimmed;
    }
  }
  return null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

// ── from apps/frontend/.../chatModel/subagentReducer.ts ──────────────────

/** Conversation-scoped subagent snapshot keyed by `task_id`. */
export type SubagentSnapshotMap = ReadonlyMap<string, SubagentEntry>;

/** Newest first, by completed_at then started_at. */
export function subagentsByRecency(
  current: SubagentSnapshotMap,
): readonly SubagentEntry[] {
  return [...current.values()].sort(byRecency);
}

function byRecency(left: SubagentEntry, right: SubagentEntry): number {
  return recencyValue(right) - recencyValue(left);
}

function recencyValue(entry: SubagentEntry): number {
  const completed = entry.completed_at ? Date.parse(entry.completed_at) : NaN;
  if (!Number.isNaN(completed)) {
    return completed;
  }
  const started = entry.started_at ? Date.parse(entry.started_at) : NaN;
  if (!Number.isNaN(started)) {
    return started;
  }
  return -Number.MAX_SAFE_INTEGER;
}

// ── from apps/frontend/.../chatModel/subagentStatus.ts ───────────────────
//
// `paused` is intentionally NOT a running state — fleet "is anything
// running" checks classify a paused subagent as not running so the pane
// badge counts only in-flight work and the progress chrome freezes.

const RUNNING_STATES: ReadonlySet<SubagentLifecycleStatus> = new Set([
  "queued",
  "running",
]);

export function isRunningStatus(status: SubagentLifecycleStatus): boolean {
  return RUNNING_STATES.has(status);
}

// ── from apps/frontend/.../utils/errors.ts ───────────────────────────────
//
// Canonical "turn a caught unknown into a user-visible string". Reproduced
// so the moved DraftTab (which surfaces PATCH/SEND/DISCARD errors) stays
// app-import-free. The fallback is required so call sites keep their domain
// context.

export function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof Error) {
    const trimmed = err.message?.trim();
    if (trimmed) return trimmed;
  }
  return fallback;
}
