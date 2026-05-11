// PR 1.1 — citation reducer that runs alongside `applyRuntimeEvent`.
//
// Citations are run-scoped, not message-scoped, so they live in their own
// `Map<runId, Map<citation_id, CitationSourceRef>>` rather than inside
// `ChatItem`. This keeps the existing reducer focused on chat content and
// lets the registry survive interleaved messages, sub-runs, and replays
// without entanglement.
//
// P7 added the batched `sources_ingested` variant — emitted by
// `CitationLedger.register_many` after the projector switches to batch
// mode (PR2 of the citation-batching refactor). Same payload shape as N
// `source_ingested` events: iterate `payload.citations` and upsert each.

import {
  isCitationSourceRef,
  isSourceIngestedPayload,
  isSourcesIngestedPayload,
  type CitationSourceRef,
  type RuntimeEventEnvelope,
} from "@enterprise-search/api-types";
import {
  emptyCitationRegistry,
  upsertCitation,
  upsertCitations,
  type CitationRegistryByRun,
} from "./citationsRegistry";

export function applyCitationEvent(
  registry: CitationRegistryByRun,
  event: RuntimeEventEnvelope,
): CitationRegistryByRun {
  if (event.event_type === "source_ingested") {
    if (!isSourceIngestedPayload(event.payload)) {
      return registry;
    }
    return upsertCitation(registry, event.run_id, event.payload.citation);
  }
  if (event.event_type === "sources_ingested") {
    if (!isSourcesIngestedPayload(event.payload)) {
      return registry;
    }
    if (event.payload.citations.length === 0) {
      return registry;
    }
    return upsertCitations(registry, event.run_id, event.payload.citations);
  }
  if (event.event_type === "final_response") {
    const citations = sealedCitationsFromPayload(event.payload);
    if (citations.length === 0) {
      return registry;
    }
    return upsertCitations(registry, event.run_id, citations);
  }
  return registry;
}

export function buildCitationRegistry(
  events: Iterable<RuntimeEventEnvelope>,
): CitationRegistryByRun {
  let registry = emptyCitationRegistry();
  for (const event of events) {
    registry = applyCitationEvent(registry, event);
  }
  return registry;
}

function sealedCitationsFromPayload(payload: unknown): CitationSourceRef[] {
  if (
    payload === null ||
    typeof payload !== "object" ||
    Array.isArray(payload)
  ) {
    return [];
  }
  const value = (payload as Record<string, unknown>).citations;
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter(isCitationSourceRef);
}
