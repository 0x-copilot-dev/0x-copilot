// PR 1.1 — inline citation chip.
//
// Rendered by the markdown link slot when a `#cite:<id>` href is detected
// (see `citationRemarkPlugin`). Resolves the citation against the active
// run's registry via React context. An unknown id renders as a muted
// placeholder so the assistant can never produce an unresolvable chip
// during streaming or after a token-retention failure on weaker models.

import type {
  CitationSourceRef,
  SourceEntry,
} from "@enterprise-search/api-types";
import { useMemo, type ReactElement } from "react";
import { useCitation } from "./citationsContext";
import { useSourcePreviewTrigger } from "./SourcePreview";

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
  // PR 3.7.2 — bridge the per-citation registry to the preview card,
  // which is typed for the aggregate `SourceEntry` shape used by the
  // Sources tab. The card only reads display fields, so we project the
  // missing aggregation fields with neutral defaults.
  const previewEntry = useMemo<SourceEntry | undefined>(
    () =>
      citation === undefined ? undefined : citationToSourceEntry(citation),
    [citation],
  );
  const previewProps = useSourcePreviewTrigger(previewEntry);
  if (citation === undefined) {
    // PR 8.0.1 — rendered as a span pill (not <sup>) so the chip sits
    // inline on the prose baseline, matching the design's pill shape.
    return (
      <span
        className="citation-chip citation-chip--unresolved"
        aria-label="Unresolved citation"
        title="This citation could not be resolved."
      >
        ?
      </span>
    );
  }
  return (
    <a
      className="citation-chip"
      data-citation-id={citation.citation_id}
      data-connector={citation.source_connector}
      href={citation.source_url ?? "#"}
      title={`${citation.title} — ${citation.source_connector}`}
      onClick={(event) => {
        if (onSelect) {
          event.preventDefault();
          onSelect(citation);
        }
      }}
      rel="noreferrer"
      target={citation.source_url ? "_blank" : undefined}
      {...previewProps}
    >
      {citation.ordinal}
    </a>
  );
}

function citationToSourceEntry(citation: CitationSourceRef): SourceEntry {
  return {
    citation_id: citation.citation_id,
    source_connector: citation.source_connector,
    source_doc_id: citation.source_doc_id,
    source_url: citation.source_url,
    title: citation.title,
    snippet: citation.snippet,
    freshness_at: citation.freshness_at,
    citation_count: 1,
    last_cited_at: "",
  };
}
