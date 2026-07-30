// runMarkdownComponents — the desktop Run cockpit's citation chip renderer.
//
// The desktop twin of `apps/frontend/src/features/run/runMarkdownComponents.tsx`.
// Both hosts must supply this, because `MarkdownText` only routes anchors to chip
// components when the host passes a `components` map; with it omitted, Streamdown
// falls through to its OWN default `<a>` renderer and the user sees:
//
//   1. the raw token text `[[8]]`, because the remark plugin rewrote the token to
//      an anchor whose LABEL is still the token, and
//   2. Streamdown's "Open external link?" confirmation popover, because
//      `#cite-ord:8` is not on its allowed-link-prefix list — so an internal
//      in-page href gets presented as a hostile outbound URL with dead
//      "Copy link" / "Open link" buttons.
//
// Neither is a design decision; both are the absence of this file. Passing these
// components makes the chip render as `8` and the popover never appear.
//
// Boundary: `@0x-copilot/chat-surface` only — the same rule the web twin follows.
// The chips resolve against the `CitationsProvider` that `RunDestination` mounts
// around its single `TcChat` (fed by the pure `projectCitations` selector), so no
// fetching or transport belongs here. `.citation-chip` styling ships from the
// package (`citations/citations.css`, imported in `bootstrap.tsx`).

import {
  CitationChip as HeadlessCitationChip,
  OrdinalCitationChip as HeadlessOrdinalCitationChip,
  createMarkdownLink,
  useCitation,
  useResolvedOrdinalCitation,
} from "@0x-copilot/chat-surface";
import type { ReactElement } from "react";

// Model-declared `[[N]]` chip: resolve the conversation ordinal against the run's
// link registry (`citation_made` events). `resolved.onSelect` — wired by the
// cockpit to focus the Sources rail — drives the click; an unresolved ordinal
// renders the muted `?` placeholder rather than inventing a source.
function RunOrdinalCitationChip({
  conversationOrdinal,
}: {
  conversationOrdinal: number;
}): ReactElement {
  const resolved = useResolvedOrdinalCitation(conversationOrdinal);
  return (
    <HeadlessOrdinalCitationChip
      conversationOrdinal={conversationOrdinal}
      resolved={resolved}
    />
  );
}

// Legacy `[c<id>]` chip: resolve the citation_id against the active-run source
// registry. No hover-preview portal on desktop (web injects one via
// `previewProps`); an unknown id renders the same muted `?`.
function RunCitationChip({ citationId }: { citationId: string }): ReactElement {
  const citation = useCitation(citationId);
  return <HeadlessCitationChip citation={citation} />;
}

/**
 * The Streamdown `components` map handed to `RunDestination.markdownComponents`.
 * Module-scope + stable so the memoized remark plugin never churns. Left inferred
 * (like the web twin) so the anchor dispatcher's `AnchorHTMLAttributes` signature
 * stays assignable to Streamdown's `components.a` slot.
 */
export const runMarkdownComponents = {
  a: createMarkdownLink({
    CitationChip: RunCitationChip,
    OrdinalCitationChip: RunOrdinalCitationChip,
  }),
};
