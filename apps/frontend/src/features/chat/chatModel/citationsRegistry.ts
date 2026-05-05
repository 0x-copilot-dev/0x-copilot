// PR 1.1 — frontend citation registry.
//
// Per-run map of citation_id -> CitationSourceRef. Built from
// `source_ingested` events (live during the run) and from
// `final_response.citations` (sealed snapshot used on archive reads). The
// Streamdown remark plugin resolves `[c<id>]` tokens against this map at
// render time; the Sources tab iterates entries by `ordinal`.
//
// Idempotent on `citation_id`: replaying a run produces the same map even
// if individual `source_ingested` events are observed twice (SSE drops +
// resume after `?after_sequence=N`).

import type { CitationSourceRef } from "@enterprise-search/api-types";

export type CitationRegistryByRun = ReadonlyMap<
  string,
  ReadonlyMap<string, CitationSourceRef>
>;

const EMPTY_RUN_REGISTRY: ReadonlyMap<string, CitationSourceRef> = new Map();

export function emptyCitationRegistry(): CitationRegistryByRun {
  return new Map();
}

export function citationsForRun(
  registry: CitationRegistryByRun,
  runId: string | null | undefined,
): ReadonlyMap<string, CitationSourceRef> {
  if (!runId) {
    return EMPTY_RUN_REGISTRY;
  }
  return registry.get(runId) ?? EMPTY_RUN_REGISTRY;
}

export function upsertCitation(
  registry: CitationRegistryByRun,
  runId: string,
  citation: CitationSourceRef,
): CitationRegistryByRun {
  const existingRun = registry.get(runId);
  // Idempotency on citation_id keeps replay/SSE-resume deterministic.
  if (existingRun?.get(citation.citation_id) !== undefined) {
    return registry;
  }
  const nextRun = new Map(existingRun);
  nextRun.set(citation.citation_id, citation);
  const next = new Map(registry);
  next.set(runId, nextRun);
  return next;
}

export function upsertCitations(
  registry: CitationRegistryByRun,
  runId: string,
  citations: readonly CitationSourceRef[],
): CitationRegistryByRun {
  if (citations.length === 0) {
    return registry;
  }
  const existingRun = registry.get(runId);
  const nextRun = new Map(existingRun);
  let changed = false;
  for (const citation of citations) {
    if (nextRun.has(citation.citation_id)) {
      continue;
    }
    nextRun.set(citation.citation_id, citation);
    changed = true;
  }
  if (!changed) {
    return registry;
  }
  const next = new Map(registry);
  next.set(runId, nextRun);
  return next;
}

export function citationsByOrdinal(
  citations: ReadonlyMap<string, CitationSourceRef>,
): readonly CitationSourceRef[] {
  return [...citations.values()].sort((a, b) => a.ordinal - b.ordinal);
}
