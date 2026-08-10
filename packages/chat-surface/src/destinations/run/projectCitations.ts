// projectCitations — pure citation projection over the single run stream (WC-P6a / AD-11).
//
// A peer of `projectSubagents` / `projectApprovals`: it reduces the ONE canonical
// `session.events` array into everything the run-scoped `CitationsProvider` needs
// to resolve in-chat citation chips, WITHOUT a second SSE subscription or a second
// `useEventProjector` (FR-3.3 / NFR-2). The provider it feeds is mounted by the
// cockpit (`RunDestination`) around the single `TcChat`, and the host supplies the
// nav-aware chip renderer (`markdownComponents`) — so the substrate stays out of
// this pure selector (AD-11).
//
// Two citation systems coexist on the wire (both emitted by the backend today):
//   - `source_ingested` / `sources_ingested` (+ `final_response.citations`) carry
//     a full `CitationSourceRef` and build the per-run source registry that the
//     legacy `[c<id>]` `CitationChip` resolves against (`useCitation`).
//   - `citation_made` carries a `CitationLink` (an ordinal → tool_call pointer)
//     and builds the link registry that the model-declared `[[N]]`
//     `OrdinalCitationChip` resolves against (`useOrdinalCitation`). This is the
//     path the runtime emits for every `[[N]]` marker (see api-types §CitationLink
//     and services/ai-backend citation_resolver).
//
// Both registries are keyed by `run_id`, so the projection is robust even if the
// stream ever carries more than one run; in the cockpit `session.events` is a
// single bound run, so in practice each registry holds one run.

import {
  isCitationSourceRef,
  isSourceIngestedPayload,
  isSourcesIngestedPayload,
  type CitationSourceRef,
  type RuntimeEventEnvelope,
} from "@0x-copilot/api-types";

import type { CitationLookup } from "../../citations/CitationsContext";
import {
  buildCitationLinkRegistry,
  type CitationLinkRegistryByRun,
} from "../../citations/linkReducer";
import {
  citationsForRun,
  emptyCitationRegistry,
  upsertCitation,
  upsertCitations,
  type CitationRegistryByRun,
} from "../../citations/registry";

/**
 * The exact set of props `CitationsProvider` consumes, derived purely from the
 * run stream. The host spreads this into the provider it mounts (or the cockpit
 * mounts it directly) and supplies the chip components + `onOrdinalSelect` nav.
 */
export interface CitationProjection {
  /** Active-run flat map (`citation_id → CitationSourceRef`) — `[c<id>]` chips. */
  readonly citations: CitationLookup;
  /** Full per-run source registry — the Sources strip's `useRunCitations`. */
  readonly byRun: CitationRegistryByRun;
  /** Runs whose `final_response` has sealed — the `sealedOnly` gate. */
  readonly terminalRuns: ReadonlySet<string>;
  /** Model-declared `[[N]]` link registry (from `citation_made`). */
  readonly linksByRun: CitationLinkRegistryByRun;
  /**
   * The run whose chips are currently being rendered — the streaming run, or
   * `null` once it seals (so `useOrdinalCitation` falls back to scanning every
   * run in the registry, which resolves chips on a completed message).
   */
  readonly activeRunId: string | null;
}

const EMPTY_LOOKUP: CitationLookup = new Map();
const EMPTY_TERMINAL: ReadonlySet<string> = new Set();

const EMPTY_PROJECTION: CitationProjection = {
  citations: EMPTY_LOOKUP,
  byRun: emptyCitationRegistry(),
  terminalRuns: EMPTY_TERMINAL,
  linksByRun: buildCitationLinkRegistry([]),
  activeRunId: null,
};

/**
 * Fold one runtime event into the per-run source registry. Mirrors the
 * host-owned `chatModel/citationReducer.applyCitationEvent` byte-for-byte (the
 * same deferral the workspace helpers apply) — `source_ingested` upserts one
 * `CitationSourceRef`, `sources_ingested` upserts the batch, and `final_response`
 * upserts its sealed citation snapshot so an archive read rebuilds chips without
 * replaying every ingest event. Any other event returns the registry unchanged.
 */
function applySourceCitationEvent(
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

/** The sealed `CitationSourceRef[]` on a `final_response` payload, if any. */
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

/**
 * Project the run stream into the `CitationsProvider` inputs. Pure over `events`
 * (referentially stable for an unchanged array via the caller's `useMemo`).
 */
export function projectCitations(
  events: readonly RuntimeEventEnvelope[],
): CitationProjection {
  if (events.length === 0) {
    return EMPTY_PROJECTION;
  }

  let byRun = emptyCitationRegistry();
  const terminalRuns = new Set<string>();
  let lastRunId: string | null = null;

  for (const event of events) {
    byRun = applySourceCitationEvent(byRun, event);
    if (typeof event.run_id === "string" && event.run_id !== "") {
      lastRunId = event.run_id;
      // `final_response` seals a run; the Sources strip's `sealedOnly` gate reads
      // this, and it also flips `activeRunId` to null so completed chips resolve
      // via the scan-all fallback rather than a stale active-run lookup.
      if (event.event_type === "final_response") {
        terminalRuns.add(event.run_id);
      }
    }
  }

  const linksByRun = buildCitationLinkRegistry(events);
  // The active run is the last streamed run while it is still open; once it seals
  // we hand `null` so `useOrdinalCitation` scans every run (the completed-message
  // case). The flat `[c<id>]` map stays keyed on the last run either way, so a
  // legacy chip on a finished message still resolves.
  const activeRunId =
    lastRunId !== null && !terminalRuns.has(lastRunId) ? lastRunId : null;
  const citations = citationsForRun(byRun, lastRunId);

  return { citations, byRun, terminalRuns, linksByRun, activeRunId };
}
