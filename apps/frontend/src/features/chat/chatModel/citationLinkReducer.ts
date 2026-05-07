// PR 1.1-rev2 — model-declared citation link reducer.
//
// Citation links are pointers (assistant_message_id + prose_offset →
// tool_invocation by conversation_ordinal). They live alongside the
// PR 1.1 per-run citation registry during the parallel rollout window.
// Once the legacy `[c<id>]` path is removed, the registry will be the
// only citation source on the FE.
//
// Keyed by (run_id, message_id) so a long conversation that interleaves
// runs and messages can resolve a chip back to its emitted ordinal
// without ambiguity. Each emission is also keyed by prose_offset to
// support a source cited multiple times in the same message.

import {
  isCitationLink,
  isCitationMadePayload,
  type CitationLink,
  type RuntimeEventEnvelope,
} from "@enterprise-search/api-types";
import { citationDebug } from "./citationDebug";

/** All resolved citation links for one assistant message, keyed by their
 *  prose offset so a re-delivered delta does not duplicate the chip. */
export type CitationLinksByOffset = ReadonlyMap<number, CitationLink>;

/** All resolved citation links for one run, keyed by message_id. */
export type CitationLinksByMessage = ReadonlyMap<string, CitationLinksByOffset>;

/** All resolved citation links for the active conversation, keyed by run_id. */
export type CitationLinkRegistryByRun = ReadonlyMap<
  string,
  CitationLinksByMessage
>;

const EMPTY_OFFSETS: CitationLinksByOffset = new Map();
const EMPTY_BY_MESSAGE: CitationLinksByMessage = new Map();
const EMPTY_REGISTRY: CitationLinkRegistryByRun = new Map();

export function emptyCitationLinkRegistry(): CitationLinkRegistryByRun {
  return EMPTY_REGISTRY;
}

export function applyCitationLinkEvent(
  registry: CitationLinkRegistryByRun,
  event: RuntimeEventEnvelope,
): CitationLinkRegistryByRun {
  if (event.event_type !== "citation_made") {
    return registry;
  }
  if (!isCitationMadePayload(event.payload)) {
    citationDebug("reducer.reject_payload run=" + event.run_id, {
      payload: event.payload,
    });
    return registry;
  }
  const link = normalizeLink(event.payload.link);
  citationDebug(
    `reducer.accept run=${event.run_id} ordinal=${link.conversation_ordinal} ` +
      `msg=${link.message_id} call_id='${link.source_tool_call_id}'`,
  );
  return upsertCitationLink(registry, event.run_id, link);
}

/** Coerce a wire-shape ``CitationLink`` to a fully-typed value.
 *
 *  ``source_tool_call_id`` may arrive as ``undefined`` or ``null`` for
 *  two reasons:
 *  - events persisted before the projector defaulted the field to
 *    ``""`` (replay path),
 *  - the resolver fired for a hallucinated ordinal the allocator
 *    never bound to a tool call.
 *  Downstream consumers (chip resolution, tool-source projection)
 *  rely on the field being a string, so coerce here once.
 */
function normalizeLink(link: CitationLink): CitationLink {
  const callId = (link as { source_tool_call_id?: string | null })
    .source_tool_call_id;
  if (typeof callId === "string") {
    return link;
  }
  return { ...link, source_tool_call_id: "" };
}

export function buildCitationLinkRegistry(
  events: Iterable<RuntimeEventEnvelope>,
): CitationLinkRegistryByRun {
  let registry: CitationLinkRegistryByRun = EMPTY_REGISTRY;
  let citationMadeCount = 0;
  for (const event of events) {
    if (event.event_type === "citation_made") {
      citationMadeCount += 1;
    }
    registry = applyCitationLinkEvent(registry, event);
  }
  citationDebug(
    `registry.replay_rebuild citation_made_events=${citationMadeCount} ` +
      `runs_in_registry=${registry.size}`,
  );
  return registry;
}

export function upsertCitationLink(
  registry: CitationLinkRegistryByRun,
  runId: string,
  link: CitationLink,
): CitationLinkRegistryByRun {
  const byMessage = registry.get(runId) ?? EMPTY_BY_MESSAGE;
  const byOffset = byMessage.get(link.message_id) ?? EMPTY_OFFSETS;
  const existing = byOffset.get(link.prose_offset);
  if (existing && citationLinksEqual(existing, link)) {
    return registry;
  }
  const nextOffsets = new Map(byOffset);
  nextOffsets.set(link.prose_offset, link);
  const nextByMessage = new Map(byMessage);
  nextByMessage.set(link.message_id, nextOffsets);
  const nextRegistry = new Map(registry);
  nextRegistry.set(runId, nextByMessage);
  return nextRegistry;
}

export function linksForMessage(
  registry: CitationLinkRegistryByRun,
  runId: string,
  messageId: string,
): CitationLinksByOffset {
  return registry.get(runId)?.get(messageId) ?? EMPTY_OFFSETS;
}

/** Find the resolved link for a given ordinal in a message — used by the
 *  ordinal-keyed chip to surface the cited tool_call_id. Returns the
 *  first link that matches; in practice there is at most one per
 *  ordinal+message but the API stays defensive. */
export function linkForOrdinal(
  registry: CitationLinkRegistryByRun,
  runId: string,
  messageId: string,
  ordinal: number,
): CitationLink | undefined {
  const byOffset = linksForMessage(registry, runId, messageId);
  for (const link of byOffset.values()) {
    if (link.conversation_ordinal === ordinal) {
      return link;
    }
  }
  return undefined;
}

/** Find any resolved link for an ordinal in any message in the run — used
 *  by the Sources tab to dedupe rows by ordinal regardless of which
 *  message in the run cited them. */
export function anyLinkForOrdinalInRun(
  registry: CitationLinkRegistryByRun,
  runId: string,
  ordinal: number,
): CitationLink | undefined {
  const byMessage = registry.get(runId);
  if (byMessage === undefined) {
    return undefined;
  }
  for (const byOffset of byMessage.values()) {
    for (const link of byOffset.values()) {
      if (link.conversation_ordinal === ordinal) {
        return link;
      }
    }
  }
  return undefined;
}

/** All resolved links for the run, flattened in (message, offset) order.
 *  Convenience for the Sources tab population path. */
export function linksForRun(
  registry: CitationLinkRegistryByRun,
  runId: string,
): readonly CitationLink[] {
  const byMessage = registry.get(runId);
  if (byMessage === undefined) {
    return [];
  }
  const out: CitationLink[] = [];
  for (const byOffset of byMessage.values()) {
    for (const link of byOffset.values()) {
      out.push(link);
    }
  }
  return out;
}

function citationLinksEqual(a: CitationLink, b: CitationLink): boolean {
  return (
    a.conversation_ordinal === b.conversation_ordinal &&
    a.message_id === b.message_id &&
    a.prose_offset === b.prose_offset &&
    a.prose_length === b.prose_length &&
    a.source_tool_call_id === b.source_tool_call_id
  );
}

/** Re-export the type guard so consumers can validate a payload without
 *  pulling in the full api-types module. */
export { isCitationLink };
