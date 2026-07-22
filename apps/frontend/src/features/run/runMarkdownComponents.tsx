// runMarkdownComponents — the web Run cockpit's citation chip renderer (WC-P6a).
//
// The cockpit (`RunDestination`) mounts a `CitationsProvider` fed by the pure
// `projectCitations` selector and threads whatever `markdownComponents` the host
// supplies into the single `TcChat`. This module is that host contribution: the
// nav-aware chip node (AD-11), built from the package's substrate-agnostic pieces
// so it depends ONLY on `@0x-copilot/chat-surface` — NOT on `features/chat`, whose
// legacy ChatScreen tree is retired in WC-P8. The two `[[N]]` / `[c<id>]` chip
// wrappers resolve against the provider the cockpit mounts (same hooks the legacy
// web wrappers call), so a citation the model emits renders as a resolved chip.
//
// Boundary: `@0x-copilot/chat-surface` only. `.citation-chip` styling is global
// (apps/frontend/src/styles.css), so no per-chunk CSS import is needed.

import {
  CitationChip as HeadlessCitationChip,
  OrdinalCitationChip as HeadlessOrdinalCitationChip,
  createMarkdownLink,
  useCitation,
  useResolvedOrdinalCitation,
} from "@0x-copilot/chat-surface";
import type { ReactElement } from "react";

// Model-declared `[[N]]` chip: resolve the ordinal against the run's link
// registry (`citation_made` events) and draw the headless chip. `resolved.onSelect`
// (when the host wires `onOrdinalSelect` on the cockpit) drives the click; else the
// chip falls back to its `#tool-call-<id>` anchor.
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
// registry and draw the headless chip. No hover-preview portal here (a Sources-tab
// follow-up); an unresolved id renders the muted `?` placeholder.
function RunCitationChip({ citationId }: { citationId: string }): ReactElement {
  const citation = useCitation(citationId);
  return <HeadlessCitationChip citation={citation} />;
}

/**
 * The Streamdown `components` map the cockpit forwards to `TcChat`
 * (`RunDestination.markdownComponents`). Module-scope + stable so the memoized
 * remark plugin never churns. Left inferred (like the legacy web `MarkdownText`
 * adapter) so the anchor dispatcher's `AnchorHTMLAttributes` signature stays
 * assignable to Streamdown's `components.a` slot.
 */
export const runMarkdownComponents = {
  a: createMarkdownLink({
    CitationChip: RunCitationChip,
    OrdinalCitationChip: RunOrdinalCitationChip,
  }),
};
