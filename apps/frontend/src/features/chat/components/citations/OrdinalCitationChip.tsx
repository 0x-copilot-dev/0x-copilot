// PR 1.1-rev2 / 04 — inline chip for model-declared ``[[N]]`` citation tokens.
//
// Rendered by the markdown link slot when a ``#cite-ord:<n>`` href is
// detected (see ``citationRemarkPlugin``). Resolves the ordinal against
// the active run's ``CitationLinkRegistryByRun`` via React context.
//
// PR 04 — every ``citation_made`` event arrives with a non-empty
// ``source_tool_call_id`` (the runtime allocator binds every ordinal
// to the LangGraph tool_call_id; the resolver stamps the binding on
// every event). The chip's ``data-citation-id`` is always
// ``tool:<source_tool_call_id>`` so the reverse-handshake helper
// (``scrollChatToCitation``) finds the chip from the Sources tab row's
// ↗ jump button without an FE-side fallback. Hallucinated ordinals
// (model wrote ``[[99]]`` for a number that was never allocated) come
// back without a binding and render as the muted ``?`` placeholder.
//
// Coexists with the legacy ``CitationChip`` during the parallel rollout
// window. Once PR 1.1's ``[c<id>]`` path is removed, the two chips
// merge or the legacy file is deleted.

import { CITATION_ORDINAL_HREF_PREFIX } from "@enterprise-search/chat-surface";
import type { ReactElement } from "react";
import { useEffect, useRef } from "react";

import { citationDebug } from "../../chatModel/citationDebug";
import { useResolvedOrdinalCitation } from "./citationsContext";

// CITATION_ORDINAL_HREF_PREFIX is the single source of truth in
// @enterprise-search/chat-surface (shared by the citation remark plugin
// that emits these hrefs and by this chip that parses them). Imported
// for local use only — deliberately NOT re-exported from here, so the
// constant has exactly one canonical import path.

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
  const resolved = useResolvedOrdinalCitation(conversationOrdinal);
  // Trace each chip's resolution outcome once per ordinal+state — first
  // mount and any subsequent state change. Helps the user diagnose
  // "chip rendered but unresolved" vs "chip never rendered".
  const lastLoggedRef = useRef<string | null>(null);
  useEffect(() => {
    const state = resolved === null ? "unresolved" : "resolved";
    const key = `${conversationOrdinal}:${state}`;
    if (lastLoggedRef.current !== key) {
      lastLoggedRef.current = key;
      citationDebug(
        `chip.${state} ordinal=${conversationOrdinal}` +
          (resolved ? ` call_id='${resolved.callId}'` : ""),
      );
    }
  }, [conversationOrdinal, resolved]);
  if (resolved === null) {
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
      data-citation-id={resolved.citationId}
      data-conversation-ordinal={String(conversationOrdinal)}
      data-source-tool-call-id={resolved.callId}
      href={`#tool-call-${resolved.callId}`}
      title={`Tool call #${conversationOrdinal}`}
      onClick={(event) => {
        // Prefer the per-chip prop (kept for tests/legacy mounts), then
        // the context callback wired by ChatScreen → workspace pane.
        if (onSelect) {
          event.preventDefault();
          onSelect(conversationOrdinal, resolved.callId);
          return;
        }
        if (resolved.onSelect) {
          event.preventDefault();
          resolved.onSelect();
        }
      }}
    >
      {conversationOrdinal}
    </a>
  );
}
