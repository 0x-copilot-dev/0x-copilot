// PR 3.1 — post-prose Sources strip beneath each assistant message.
//
// PR-1.4 — hoisted into @0x-copilot/chat-surface. Pure presentational
// renderer over `CitationSourceRef[]` (design-system + api-types only, no
// substrate primitive, no app import); the host feeds it the per-run
// citations it reads via `useRunCitations`.
//
// One chip-button per cited source for *this run*. Reads the
// per-run citation registry that PR 1.1's `applyCitationEvent` builds
// (live during a stream) or that `buildCitationRegistry(replayEvents)`
// rebuilds on history load. No new fetch, no new event.
//
// Click behavior calls onSelect(citation_id); ChatScreen forwards to
// `useDetailsPanel().open('sources', { focusCitationId })` (existing
// slash-overlay path) until PR 3.2 mounts the right-rail pane and takes
// over the focus handshake.

import type { CitationSourceRef } from "@0x-copilot/api-types";
import { AppIcon, classNames } from "@0x-copilot/design-system";
import type { ReactElement } from "react";

export interface MessageSourcesStripProps {
  citations: readonly CitationSourceRef[];
  onSelect?: (citation: CitationSourceRef) => void;
}

export function MessageSourcesStrip({
  citations,
  onSelect,
}: MessageSourcesStripProps): ReactElement | null {
  if (citations.length === 0) {
    return null;
  }
  const ordered = [...citations].sort((a, b) => a.ordinal - b.ordinal);
  return (
    <div
      className="atlas-sources-strip"
      role="list"
      aria-label="Sources cited in this answer"
    >
      <span className="atlas-sources-strip__label">Sources</span>
      {ordered.map((citation) => (
        <button
          key={citation.citation_id}
          type="button"
          role="listitem"
          className={classNames(
            "atlas-sources-strip__chip",
            `atlas-sources-strip__chip--${citation.source_connector}`,
          )}
          data-connector={citation.source_connector}
          onClick={() => onSelect?.(citation)}
          title={`${citation.title} — ${citation.source_connector}`}
          aria-label={`Open citation ${citation.ordinal} — ${citation.title}`}
        >
          <span className="atlas-sources-strip__num">{citation.ordinal}</span>
          <AppIcon
            name={citation.source_connector}
            size="sm"
            className="atlas-sources-strip__glyph"
          />
          <span className="atlas-sources-strip__title">{citation.title}</span>
        </button>
      ))}
    </div>
  );
}
