// PR 1.1 — inline citation chip.
//
// Rendered by the markdown link slot when a `#cite:<id>` href is detected
// (see `citationRemarkPlugin`). Resolves the citation against the active
// run's registry via React context. An unknown id renders as a muted
// placeholder so the assistant can never produce an unresolvable chip
// during streaming or after a token-retention failure on weaker models.

import type { CitationSourceRef } from "@enterprise-search/api-types";
import type { ReactElement } from "react";
import { useCitation } from "./citationsContext";

export const CITATION_HREF_PREFIX = "#cite:";

export function isCitationHref(href: string | undefined): boolean {
  return typeof href === "string" && href.startsWith(CITATION_HREF_PREFIX);
}

export function citationIdFromHref(href: string): string | null {
  if (!href.startsWith(CITATION_HREF_PREFIX)) {
    return null;
  }
  const id = href.slice(CITATION_HREF_PREFIX.length);
  return id || null;
}

export function CitationChip({
  citationId,
  onSelect,
}: {
  citationId: string;
  onSelect?: (citation: CitationSourceRef) => void;
}): ReactElement {
  const citation = useCitation(citationId);
  if (citation === undefined) {
    return (
      <sup
        className="citation-chip citation-chip--unresolved"
        aria-label="Unresolved citation"
        title="This citation could not be resolved."
      >
        ?
      </sup>
    );
  }
  return (
    <sup
      className="citation-chip"
      data-connector={citation.source_connector}
      title={`${citation.title} — ${citation.source_connector}`}
    >
      <a
        href={citation.source_url ?? "#"}
        onClick={(event) => {
          if (onSelect) {
            event.preventDefault();
            onSelect(citation);
          }
        }}
        rel="noreferrer"
        target={citation.source_url ? "_blank" : undefined}
      >
        {citation.ordinal}
      </a>
    </sup>
  );
}
