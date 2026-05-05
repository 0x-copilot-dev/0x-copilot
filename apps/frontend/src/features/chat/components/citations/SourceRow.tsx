// PR 3.1 — shared presentational row for the Sources surface.
//
// Two consumers:
//   1. <SourcesPanel> — slash-command `/sources` overlay (PR 1.1 / PR 1.5)
//   2. <SourcesTab>   — right-rail Sources tab (PR 3.2 host)
//
// One component, one set of styles, one behavior. Promoting this to
// design-system would be premature (per packages/design-system/CLAUDE.md
// "feature workflows stay here"); when a third consumer appears, revisit.

import type { SourceEntry } from "@enterprise-search/api-types";
import {
  AppIcon,
  Badge,
  Card,
  classNames,
} from "@enterprise-search/design-system";
import { forwardRef, type ReactElement, type Ref } from "react";

export interface SourceRowProps {
  source: SourceEntry;
  ordinal: number; // 1-based; the chip number to display
  focused?: boolean;
  onSelect?: (source: SourceEntry) => void;
}

export const SourceRow = forwardRef(function SourceRow(
  { source, ordinal, focused, onSelect }: SourceRowProps,
  ref: Ref<HTMLLIElement>,
): ReactElement {
  const hasUrl =
    typeof source.source_url === "string" && source.source_url.length > 0;
  const title = source.title ?? source.source_doc_id;
  const handleSelect = (): void => {
    onSelect?.(source);
  };
  return (
    <li
      ref={ref}
      data-citation-id={source.citation_id}
      data-connector={source.source_connector}
      className={classNames(
        "atlas-source-row",
        focused ? "atlas-source-row--focused" : null,
      )}
    >
      <Card tone="default">
        <button
          type="button"
          className="atlas-source-row__head"
          onClick={handleSelect}
          aria-label={`Open citation ${ordinal} — ${title} from ${source.source_connector}`}
        >
          <Badge tone="accent">{`[${ordinal}]`}</Badge>
          <AppIcon
            name={source.source_connector}
            size="sm"
            className="atlas-source-row__glyph"
          />
          <span className="atlas-source-row__title">
            {hasUrl ? (
              <a
                href={source.source_url ?? "#"}
                rel="noreferrer"
                target="_blank"
                onClick={(event) => event.stopPropagation()}
              >
                {title}
              </a>
            ) : (
              title
            )}
          </span>
          <Badge tone="neutral">{source.source_connector}</Badge>
        </button>
        {source.snippet ? (
          <p className="atlas-source-row__snippet">{source.snippet}</p>
        ) : null}
        <p className="atlas-source-row__footnote">
          {source.citation_count > 1
            ? `Cited ${source.citation_count}× · `
            : null}
          {source.freshness_at
            ? `Updated ${formatFreshness(source.freshness_at)}`
            : `Last cited ${formatFreshness(source.last_cited_at)}`}
        </p>
      </Card>
    </li>
  );
});

function formatFreshness(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return date.toLocaleString();
}
