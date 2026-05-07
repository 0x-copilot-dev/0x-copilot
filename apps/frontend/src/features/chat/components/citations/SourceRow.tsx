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
import { Badge, Card, classNames } from "@enterprise-search/design-system";
import { forwardRef, type ReactElement, type Ref } from "react";
import { humanizeConnector } from "./connectorLabel";
import { SourceFavicon } from "./SourceFavicon";
import { useSourcePreviewTrigger } from "./SourcePreview";
import { sourceFreshnessLabel } from "./sourceFreshness";

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
  const previewProps = useSourcePreviewTrigger(source);
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
          <span
            className="atlas-source-row__glyph-trigger"
            tabIndex={0}
            {...previewProps}
          >
            <SourceFavicon
              source={source}
              size="sm"
              className="atlas-source-row__glyph"
            />
          </span>
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
          <Badge tone="neutral">
            {humanizeConnector(source.source_connector)}
          </Badge>
        </button>
        {source.snippet ? (
          <p className="atlas-source-row__snippet">{source.snippet}</p>
        ) : null}
        <p className="atlas-source-row__footnote">
          {source.citation_count > 1
            ? `Cited ${source.citation_count}× · `
            : null}
          {sourceFreshnessLabel({
            connectorSlug: source.source_connector,
            freshnessAt: source.freshness_at,
            lastCitedAt: source.last_cited_at,
          })}
        </p>
      </Card>
    </li>
  );
});
