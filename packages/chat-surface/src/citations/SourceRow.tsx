// Shared presentational row for the Sources surface (panel + tab).
//
// Headless renderer. The web substrate hover-preview wiring lands here
// as `previewProps` from the caller — apps/frontend's web adapter
// (apps/frontend/src/features/chat/components/citations/SourceRow.tsx)
// resolves `useSourcePreviewTrigger(source)` and passes the result. The
// desktop substrate will write a parallel adapter that resolves against
// its own preview mechanism (or omits the preview entirely); this file
// doesn't change.

import type { SourceEntry } from "@enterprise-search/api-types";
import { Badge, Card, classNames } from "@enterprise-search/design-system";
import {
  forwardRef,
  type HTMLAttributes,
  type ReactElement,
  type Ref,
  useState,
} from "react";

import { humanizeConnector } from "./connectorLabel";
import { sourceFreshnessLabel } from "./sourceFreshness";
import { SourceFavicon } from "./SourceFavicon";

export interface SourceRowProps {
  readonly source: SourceEntry;
  /** 1-based; the chip number to display. */
  readonly ordinal: number;
  readonly focused?: boolean;
  readonly onSelect?: (source: SourceEntry) => void;
  /**
   * Substrate-owned hover-preview wiring (mouse/focus handlers, aria
   * attributes) — spread onto the favicon trigger span. Web wires this
   * via `useSourcePreviewTrigger`; desktop may omit. Optional so the
   * row is usable in tests and contexts without a preview.
   */
  readonly previewProps?: HTMLAttributes<HTMLElement>;
  /**
   * Invoked when the ↗ icon is clicked. The host scrolls the chat
   * viewport to the first chip that cited this source and pulse-flashes
   * it. Optional — when omitted the button is hidden.
   */
  readonly onJumpToChat?: (source: SourceEntry) => void;
}

export const SourceRow = forwardRef(function SourceRow(
  {
    source,
    ordinal,
    focused,
    onSelect,
    previewProps,
    onJumpToChat,
  }: SourceRowProps,
  ref: Ref<HTMLLIElement>,
): ReactElement {
  const hasUrl =
    typeof source.source_url === "string" && source.source_url.length > 0;
  const title = source.title ?? source.source_doc_id;
  const handleSelect = (): void => {
    onSelect?.(source);
  };
  const [expanded, setExpanded] = useState(false);
  const hasSnippet =
    typeof source.snippet === "string" && source.snippet.length > 0;
  return (
    <li
      ref={ref}
      data-citation-id={source.citation_id}
      data-connector={source.source_connector}
      className={classNames(
        "atlas-source-row",
        focused ? "atlas-source-row--focused" : null,
        expanded ? "atlas-source-row--expanded" : null,
      )}
    >
      <Card tone="default">
        <div className="atlas-source-row__top">
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
          <div
            className="atlas-source-row__actions"
            onClick={(event) => event.stopPropagation()}
          >
            {hasSnippet ? (
              <button
                type="button"
                className="atlas-source-row__action"
                aria-expanded={expanded}
                aria-label={
                  expanded ? "Collapse source details" : "Expand source details"
                }
                title={expanded ? "Collapse" : "Expand"}
                onClick={() => setExpanded((prev) => !prev)}
              >
                <span aria-hidden="true">{expanded ? "▾" : "▸"}</span>
              </button>
            ) : null}
            {onJumpToChat ? (
              <button
                type="button"
                className="atlas-source-row__action"
                aria-label={`Jump to where citation ${ordinal} appears in chat`}
                title="Jump to citation in chat"
                onClick={() => onJumpToChat(source)}
              >
                <span aria-hidden="true">↗</span>
              </button>
            ) : null}
          </div>
        </div>
        {hasSnippet ? (
          <p
            className={classNames(
              "atlas-source-row__snippet",
              expanded ? "atlas-source-row__snippet--full" : null,
            )}
          >
            {source.snippet}
          </p>
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
