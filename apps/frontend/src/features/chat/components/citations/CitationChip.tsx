// Web wrapper around the substrate-agnostic CitationChip in
// @0x-copilot/chat-surface.
//
// The headless chip is a pure renderer — it takes resolved citation
// data as a prop. This wrapper owns the web-substrate-specific bits:
//
//   1. Resolves `citationId` against the active-run registry via the
//      existing `useCitation` hook (apps/frontend's CitationsProvider).
//   2. Computes the SourceEntry projection the preview portal expects.
//   3. Calls `useSourcePreviewTrigger` to get hover/focus handlers and
//      hands them to the chip as `previewProps`.
//
// The desktop substrate will write a parallel wrapper that resolves
// against an extension-host bridge and (optionally) omits the preview
// trigger entirely. The chat-surface chip itself doesn't change.

import type { CitationSourceRef, SourceEntry } from "@0x-copilot/api-types";
import { CitationChip as HeadlessCitationChip } from "@0x-copilot/chat-surface";
import { useMemo, type ReactElement } from "react";

import { useCitation } from "./citationsContext";
import { useSourcePreviewTrigger } from "./SourcePreview";

export function CitationChip({
  citationId,
  onSelect,
}: {
  citationId: string;
  onSelect?: (citation: CitationSourceRef) => void;
}): ReactElement {
  const citation = useCitation(citationId);
  // PR 3.7.2 — bridge the per-citation registry to the preview card,
  // which is typed for the aggregate SourceEntry shape used by the
  // Sources tab. The card only reads display fields, so we project the
  // missing aggregation fields with neutral defaults.
  const previewEntry = useMemo<SourceEntry | undefined>(
    () =>
      citation === undefined ? undefined : citationToSourceEntry(citation),
    [citation],
  );
  const previewProps = useSourcePreviewTrigger(previewEntry);
  return (
    <HeadlessCitationChip
      citation={citation}
      onSelect={onSelect}
      previewProps={previewProps}
    />
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
