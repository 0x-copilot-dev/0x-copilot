// PR 1.1-rev2 — inline chip for model-declared ``[[N]]`` citation tokens.
//
// Rendered by the markdown link slot when a ``#cite-ord:<n>`` href is
// detected (see ``citationRemarkPlugin``). Resolves the ordinal against
// the active run's ``CitationLinkRegistryByRun`` via React context.
// An ordinal that has not (yet) been resolved by a ``citation_made``
// event renders as the same muted placeholder used by the legacy chip
// — so the assistant can never produce an unresolvable visible chip
// during streaming or when a weaker model drops the marker.
//
// Coexists with the legacy ``CitationChip`` during the parallel rollout
// window. Once PR 1.1's ``[c<id>]`` path is removed, the two chips
// merge or the legacy file is deleted.

import type { ReactElement } from "react";
import { useEffect, useRef } from "react";
import { useOrdinalCitation } from "./citationsContext";
import { citationDebug } from "../../chatModel/citationDebug";

export const CITATION_ORDINAL_HREF_PREFIX = "#cite-ord:";

/** Returns ``true`` for hrefs the ordinal chip should claim. */
export function isOrdinalCitationHref(href: string | undefined): boolean {
  return (
    typeof href === "string" && href.startsWith(CITATION_ORDINAL_HREF_PREFIX)
  );
}

/** Parse the ordinal from a ``#cite-ord:<n>`` href, or return ``null`` when
 *  the href is malformed. */
export function ordinalFromHref(href: string): number | null {
  if (!href.startsWith(CITATION_ORDINAL_HREF_PREFIX)) {
    return null;
  }
  const raw = href.slice(CITATION_ORDINAL_HREF_PREFIX.length);
  if (raw.length === 0) {
    return null;
  }
  const value = Number.parseInt(raw, 10);
  if (!Number.isInteger(value) || value <= 0 || String(value) !== raw) {
    return null;
  }
  return value;
}

export interface OrdinalCitationChipProps {
  conversationOrdinal: number;
  onSelect?: (ordinal: number, sourceToolCallId: string) => void;
}

export function OrdinalCitationChip({
  conversationOrdinal,
  onSelect,
}: OrdinalCitationChipProps): ReactElement {
  const link = useOrdinalCitation(conversationOrdinal);
  // Trace each chip's resolution outcome once per ordinal+state — first
  // mount and any subsequent state change. Helps the user diagnose
  // "chip rendered but unresolved" vs "chip never rendered".
  const lastLoggedRef = useRef<string | null>(null);
  useEffect(() => {
    const state = link === undefined ? "unresolved" : "resolved";
    const key = `${conversationOrdinal}:${state}`;
    if (lastLoggedRef.current !== key) {
      lastLoggedRef.current = key;
      citationDebug(
        `chip.${state} ordinal=${conversationOrdinal}` +
          (link
            ? ` call_id='${link.source_tool_call_id}' msg=${link.message_id}`
            : ""),
      );
    }
  }, [conversationOrdinal, link]);
  if (link === undefined) {
    return (
      <span
        className="citation-chip citation-chip--unresolved"
        aria-label={`Unresolved citation [[${conversationOrdinal}]]`}
        title="This citation could not be resolved."
      >
        ?
      </span>
    );
  }
  return (
    <a
      className="citation-chip"
      data-conversation-ordinal={String(conversationOrdinal)}
      data-source-tool-call-id={link.source_tool_call_id || undefined}
      href={`#tool-call-${link.source_tool_call_id || conversationOrdinal}`}
      title={`Tool call #${conversationOrdinal}`}
      onClick={(event) => {
        if (onSelect) {
          event.preventDefault();
          onSelect(conversationOrdinal, link.source_tool_call_id);
        }
      }}
    >
      {conversationOrdinal}
    </a>
  );
}
