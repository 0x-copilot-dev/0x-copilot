/**
 * Sources panel — PR 1.1 follow-up E.
 *
 * Lists every citation the active conversation has accumulated (live during
 * a running turn, sealed after `final_response`). Reads from the same
 * citations registry the inline `<CitationChip />` resolves against — no
 * extra round-trip, no duplicate state.
 *
 * The full Workspace pane right-rail (W3.2) will eventually host a richer
 * Sources tab alongside Agents / Draft / Approvals / Skills. Until then
 * this panel rides the existing slide-out `DetailsPanelHost` so users have
 * a verifiable view today.
 */

import {
  Badge,
  Button,
  Card,
  classNames,
} from "@enterprise-search/design-system";
import type { CitationSourceRef } from "@enterprise-search/api-types";
import type { ReactElement } from "react";

import { citationsByOrdinal } from "../../chatModel/citationsRegistry";
import type { CitationLookup } from "../citations/citationsContext";

export interface SourcesPanelProps {
  citations: CitationLookup;
  onClose: () => void;
}

export function SourcesPanel({
  citations,
  onClose,
}: SourcesPanelProps): ReactElement {
  const ordered = citationsByOrdinal(citations);
  return (
    <aside className="details-panel" data-testid="sources-panel">
      <header className="details-panel__header">
        <div>
          <h2>Sources</h2>
          <p className="details-panel__subtitle">
            {ordered.length === 0
              ? "Sources will appear here as Atlas finds them."
              : `${ordered.length} source${ordered.length === 1 ? "" : "s"} cited.`}
          </p>
        </div>
        <div className="details-panel__header-actions">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onClose}
            aria-label="Close sources panel"
          >
            ✕
          </Button>
        </div>
      </header>
      {ordered.length === 0 ? (
        <p className="details-panel__empty">
          No citations yet. Start a turn that touches a connector.
        </p>
      ) : (
        <ul className="details-panel__list" data-testid="sources-panel-list">
          {ordered.map((source) => (
            <SourceRow key={source.citation_id} source={source} />
          ))}
        </ul>
      )}
    </aside>
  );
}

function SourceRow({ source }: { source: CitationSourceRef }): ReactElement {
  const hasUrl = source.source_url !== null && source.source_url.length > 0;
  return (
    <li className={classNames("details-panel__list-item")}>
      <Card tone="default">
        <div className="details-panel__row">
          <Badge tone="accent">{`[c${source.ordinal}]`}</Badge>
          <span className="details-panel__row-title">
            {hasUrl ? (
              <a
                href={source.source_url ?? "#"}
                rel="noreferrer"
                target="_blank"
              >
                {source.title}
              </a>
            ) : (
              source.title
            )}
          </span>
          <Badge tone="neutral">{source.source_connector}</Badge>
        </div>
        {source.snippet ? (
          <p className="details-panel__subtitle">{source.snippet}</p>
        ) : null}
        {source.freshness_at ? (
          <p className="details-panel__footnote">
            Updated {formatFreshness(source.freshness_at)}
          </p>
        ) : null}
      </Card>
    </li>
  );
}

function formatFreshness(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return date.toLocaleString();
}
