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

import type {
  SourceEntry,
  SubagentEntry,
  SubagentLifecycleStatus,
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
