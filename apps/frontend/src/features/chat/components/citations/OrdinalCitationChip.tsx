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
import {
  useOrdinalCitation,
  useResolvedOrdinalCitation,
} from "./citationsContext";
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
  // PR 1.1-rev2 — resolve via the link first, ordinal-position fallback
  // second. Decoupled from ``link === undefined`` so the chip can still
  // open Sources when a citation_made event was empty (LangChain /
  // MCP-middleware paths) and the ordinal-position fallback knows the
  // call_id.
  const resolved = useResolvedOrdinalCitation(conversationOrdinal);
  // Trace each chip's resolution outcome once per ordinal+state — first
  // mount and any subsequent state change. Helps the user diagnose
  // "chip rendered but unresolved" vs "chip never rendered".
  const lastLoggedRef = useRef<string | null>(null);
  useEffect(() => {
    const state =
      link === undefined && resolved === null
        ? "unresolved"
        : link === undefined
          ? "fallback"
          : "resolved";
    const key = `${conversationOrdinal}:${state}`;
    if (lastLoggedRef.current !== key) {
      lastLoggedRef.current = key;
      citationDebug(
        `chip.${state} ordinal=${conversationOrdinal}` +
          (link
            ? ` call_id='${link.source_tool_call_id}' msg=${link.message_id}`
            : resolved
              ? ` fallback_call_id='${resolved.callId}'`
              : ""),
      );
    }
  }, [conversationOrdinal, link, resolved]);
  if (link === undefined && resolved === null) {
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
  const callId = resolved?.callId ?? link?.source_tool_call_id ?? "";
  // ``data-citation-id`` matches the synthetic id used by ``SourceRow``
  // (``tool:<call_id>``) so the reverse handshake helper
  // ``scrollChatToCitation`` can find this chip from the Sources tab's
  // ↗ jump button.
  const dataCitationId = callId
    ? `tool:${callId}`
    : `tool-ord:${conversationOrdinal}`;
  return (
    <a
      className="citation-chip"
      data-citation-id={dataCitationId}
      data-conversation-ordinal={String(conversationOrdinal)}
      data-source-tool-call-id={callId || undefined}
      href={`#tool-call-${callId || conversationOrdinal}`}
      title={`Tool call #${conversationOrdinal}`}
      onClick={(event) => {
        // Prefer the per-chip prop (kept for tests/legacy mounts), then
        // the context callback wired by ChatScreen → workspace pane.
        if (onSelect) {
          event.preventDefault();
          onSelect(conversationOrdinal, callId);
          return;
        }
        if (resolved?.onSelect) {
          event.preventDefault();
          resolved.onSelect();
        }
      }}
    >
      {conversationOrdinal}
    </a>
  );
}
