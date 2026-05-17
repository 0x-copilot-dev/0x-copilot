// PR 1.1 — inline citation chip for the legacy `[c<id>]` token format.
//
// Headless renderer. The substrate (web today, desktop tomorrow) is
// responsible for resolving `citationId` → `CitationSourceRef` and for
// any hover-preview wiring; this component just draws either the
// resolved chip or the muted "?" placeholder. Keeping the chip
// substrate-agnostic means the same visual is used on both substrates
// without re-implementing the lookup or preview portal.

import type { CitationSourceRef } from "@enterprise-search/api-types";
import type { AnchorHTMLAttributes, ReactElement } from "react";

export interface CitationChipProps {
  /** Resolved citation. When `undefined`, the chip renders the placeholder. */
  readonly citation: CitationSourceRef | undefined;
  /** Click handler — if provided, the chip suppresses default anchor navigation. */
  readonly onSelect?: (citation: CitationSourceRef) => void;
  /**
   * Substrate-owned hover-preview wiring (mouse/focus handlers, aria
   * attributes) — spread onto the chip anchor. Web wires this via
   * `useSourcePreviewTrigger`; desktop may omit. Optional by design so
   * the chip is usable in tests and contexts that don't want a preview.
   */
  readonly previewProps?: AnchorHTMLAttributes<HTMLAnchorElement>;
}

export function CitationChip({
  citation,
  onSelect,
  previewProps,
}: CitationChipProps): ReactElement {
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
