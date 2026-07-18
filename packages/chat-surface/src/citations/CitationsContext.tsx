// PR 1.1 / 3.1 / 3.5 / 04 — React context that exposes the run-scoped citation
// registry to the markdown chip resolver and to the post-prose Sources strip.
//
// PR-1.4 — hoisted into @0x-copilot/chat-surface so web and desktop resolve
// citation chips against the same registries. Pure React context over the
// hoisted citation (`./registry`) and link (`./linkReducer`) registries; no
// substrate-specific dependency, no app import. The host binds the concrete
// registries as provider props (the resolution seam lives here, not in the
// chip renderers).
//
// Two read seams over the same data:
//   - `useCitation(citation_id)` — flat lookup used by the Streamdown remark
//     plugin to resolve `[c<id>]` tokens against the *active run* (the run
//     whose chips are currently being rendered).
//   - `useRunCitations(run_id, { sealedOnly })` — per-run ordered list used
//     by `MessageSourcesStrip` so each assistant message can render its own
//     citations (PR 3.5 G9). `sealedOnly` returns `[]` until the run is
//     terminal — the strip is a summary affordance, not a live shimmer; the
//     inline chips already handle the live case.
//
// PR 04 — model-declared `[[N]]` chips resolve via `useOrdinalCitation`
// against the persistent binding map streamed in `citation_made` events.
// Every event arrives with a non-empty `source_tool_call_id` (the runtime
// allocator now binds every ordinal to the LangGraph tool_call_id); the
// FE no longer carries an ordinal-position fallback.
//
// State is owned by ChatScreen; this file is the read API.

import {
  createContext,
  useContext,
  useMemo,
  type ReactElement,
  type ReactNode,
} from "react";
import type { CitationLink, CitationSourceRef } from "@0x-copilot/api-types";
import {
  citationsByOrdinal,
  citationsForRun,
  emptyCitationRegistry,
  type CitationRegistryByRun,
} from "./registry";
import {
  anyLinkForOrdinalInRun,
  emptyCitationLinkRegistry,
  type CitationLinkRegistryByRun,
} from "./linkReducer";

export type CitationLookup = ReadonlyMap<string, CitationSourceRef>;

interface CitationsContextValue {
  /** Active-run citation map — used by inline chip resolution. */
  active: CitationLookup;
  /** Full per-run registry — used by `useRunCitations` for the Sources strip. */
  byRun: CitationRegistryByRun;
  /** Run ids whose final_response has sealed (used as the `sealedOnly` gate). */
  terminalRuns: ReadonlySet<string>;
  /** PR 1.1-rev2 — model-declared citation links keyed by run + message_id.
   *  Resolves the ``[[N]]`` chip format. Coexists with ``active`` /
   *  ``byRun`` during the parallel rollout window. */
  linksByRun: CitationLinkRegistryByRun;
  /** The run whose chips are currently being rendered — used by ordinal
   *  lookups so the correct run's link map is consulted. */
  activeRunId: string | null;
  /** PR 1.1-rev2 — invoked when an ordinal chip is clicked. Receives the
   *  resolved synthetic ``citation_id`` (``tool:<source_tool_call_id>``)
   *  so the host can open the Sources tab and scroll to the matching
   *  row. ``null`` when click should fall back to anchor navigation. */
  onOrdinalSelect: ((citationId: string) => void) | null;
}

const EMPTY_LOOKUP: CitationLookup = new Map();
const EMPTY_TERMINAL: ReadonlySet<string> = new Set();
const DEFAULT_VALUE: CitationsContextValue = {
  active: EMPTY_LOOKUP,
  byRun: emptyCitationRegistry(),
  terminalRuns: EMPTY_TERMINAL,
  linksByRun: emptyCitationLinkRegistry(),
  activeRunId: null,
  onOrdinalSelect: null,
};
const EMPTY_CITATIONS: readonly CitationSourceRef[] = [];

const CitationsContext = createContext<CitationsContextValue>(DEFAULT_VALUE);

export interface CitationsProviderProps {
  /** Active-run citation map — drives chip resolution from prose. */
  citations: CitationLookup;
  /**
   * Full per-run registry. Optional so existing tests / call sites that
   * only care about `useCitation` keep compiling. When omitted, per-run
   * lookups return empty.
   */
  byRun?: CitationRegistryByRun;
  /**
   * Run ids whose `final_response` event has fired. Optional for the same
   * reason as `byRun`. When omitted, `useRunCitations` with `sealedOnly`
   * always returns empty.
   */
  terminalRuns?: ReadonlySet<string>;
  /**
   * PR 1.1-rev2 — full per-run citation link registry (``[[N]]`` chips).
   * Optional so call sites that only render legacy chips keep compiling.
   */
  linksByRun?: CitationLinkRegistryByRun;
  /**
   * PR 1.1-rev2 — the run whose chips are currently being rendered. Used
   * by ``useOrdinalCitation`` to look up the right run's link map.
   * Optional; defaults to ``null`` in which case the ordinal hook
   * returns ``undefined``.
   */
  activeRunId?: string | null;
  /**
   * PR 1.1-rev2 — invoked when an ordinal chip is clicked, with the
   * resolved synthetic citation_id (``tool:<source_tool_call_id>``).
   * Optional; when absent the chip falls back to plain anchor navigation.
   */
  onOrdinalSelect?: (citationId: string) => void;
  children: ReactNode;
}

export function CitationsProvider({
  citations,
  byRun,
  terminalRuns,
  linksByRun,
  activeRunId,
  onOrdinalSelect,
  children,
}: CitationsProviderProps): ReactElement {
  const value = useMemo<CitationsContextValue>(
    () => ({
      active: citations,
      byRun: byRun ?? emptyCitationRegistry(),
      terminalRuns: terminalRuns ?? EMPTY_TERMINAL,
      linksByRun: linksByRun ?? emptyCitationLinkRegistry(),
      activeRunId: activeRunId ?? null,
      onOrdinalSelect: onOrdinalSelect ?? null,
    }),
    [citations, byRun, terminalRuns, linksByRun, activeRunId, onOrdinalSelect],
  );
  return (
    <CitationsContext.Provider value={value}>
      {children}
    </CitationsContext.Provider>
  );
}

export function useCitation(citationId: string): CitationSourceRef | undefined {
  return useContext(CitationsContext).active.get(citationId);
}

/**
 * PR 3.5 / G9 — per-run citation list for the post-prose Sources strip.
 *
 * Returns `[]` when:
 *   - `runId` is undefined / null (e.g. an optimistic message),
 *   - the run hasn't been seen by the registry,
 *   - `sealedOnly` is true and the run hasn't sealed `final_response` yet.
 *
 * The returned array is sorted by `ordinal` so chip-rows render in the
 * same order across the inline chips and the strip.
 */
export function useRunCitations(
  runId: string | null | undefined,
  options: { sealedOnly?: boolean } = {},
): readonly CitationSourceRef[] {
  const { byRun, terminalRuns } = useContext(CitationsContext);
  return useMemo(() => {
    if (!runId) {
      return EMPTY_CITATIONS;
    }
    if (options.sealedOnly && !terminalRuns.has(runId)) {
      return EMPTY_CITATIONS;
    }
    const map = citationsForRun(byRun, runId);
    if (map.size === 0) {
      return EMPTY_CITATIONS;
    }
    return citationsByOrdinal(map);
  }, [byRun, options.sealedOnly, runId, terminalRuns]);
}

/**
 * PR 04 — resolve an ordinal to a stable synthetic ``citation_id``
 * (``tool:<source_tool_call_id>``) plus the on-click callback the chip
 * should fire.
 *
 * Sourcing: the ``citation_made`` link's ``source_tool_call_id``,
 * stamped server-side from the canonical binding map. There is no FE
 * fallback — an empty ``source_tool_call_id`` means a hallucinated
 * ordinal and the chip surfaces as ``?`` (handled by
 * ``OrdinalCitationChip``).
 *
 * Returns ``null`` for hallucinated / out-of-range ordinals so the chip
 * stays inert.
 */
export interface ResolvedOrdinalCitation {
  citationId: string;
  callId: string;
  onSelect: (() => void) | null;
}

export function useResolvedOrdinalCitation(
  conversationOrdinal: number,
): ResolvedOrdinalCitation | null {
  const link = useOrdinalCitation(conversationOrdinal);
  const { onOrdinalSelect } = useContext(CitationsContext);
  return useMemo(() => {
    const callId = link?.source_tool_call_id ?? "";
    if (!callId) {
      return null;
    }
    const citationId = `tool:${callId}`;
    const onSelect = onOrdinalSelect ? () => onOrdinalSelect(citationId) : null;
    return { citationId, callId, onSelect };
  }, [link, onOrdinalSelect]);
}

/**
 * PR 1.1-rev2 — resolve an ordinal-keyed citation in the active run.
 *
 * Returns the underlying ``CitationLink`` (with the cited
 * ``source_tool_call_id``) when the resolver has fired a
 * ``citation_made`` event for this ordinal in any run currently
 * indexed in the registry, or ``undefined`` for hallucinated /
 * unresolved ordinals. The chip renders a muted placeholder for the
 * latter.
 *
 * Lookup order:
 * 1. ``activeRunId`` if set (mid-stream).
 * 2. Otherwise scan every run in the registry — when the run has
 *    completed (``activeRunId`` reset to ``null``) but the chips are
 *    still on screen, the chip needs to keep resolving. This mirrors
 *    ``activeCitations``'s ``mostRecentAssistantRunId`` fallback used
 *    for legacy ``[c<id>]`` chips.
 */
export function useOrdinalCitation(
  conversationOrdinal: number,
): CitationLink | undefined {
  const { linksByRun, activeRunId } = useContext(CitationsContext);
  return useMemo(() => {
    if (activeRunId !== null) {
      const found = anyLinkForOrdinalInRun(
        linksByRun,
        activeRunId,
        conversationOrdinal,
      );
      if (found !== undefined) {
        return found;
      }
    }
    // Fallback: scan every run (typical case after the assistant
    // message completes — ``activeRunId`` is null but the chips are
    // still rendered against the persisted message).
    for (const runId of linksByRun.keys()) {
      const link = anyLinkForOrdinalInRun(
        linksByRun,
        runId,
        conversationOrdinal,
      );
      if (link !== undefined) {
        return link;
      }
    }
    return undefined;
  }, [linksByRun, activeRunId, conversationOrdinal]);
}
