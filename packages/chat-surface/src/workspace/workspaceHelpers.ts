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
  isSourceIngestedPayload,
  isSourcesIngestedPayload,
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
