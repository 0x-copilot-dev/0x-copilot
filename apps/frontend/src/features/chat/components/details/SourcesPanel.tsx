/**
 * Sources panel — slash-command (`/sources`) overlay host.
 *
 * Reads from the per-source `SourceEntryMap` owned by ChatScreen — the
 * same map that powers PR 3.2's right-rail Sources tab. Live updates
 * flow through `applySourceEvent` (PR 1.5) on every `source_ingested`
 * event; the archive seed comes from `useArchivedSources` (PR 3.1).
 *
 * The body uses the shared `<SourceRow />` primitive so the slash overlay
 * and the right-rail tab render identical rows.
 */

import { Button } from "@enterprise-search/design-system";
import type { ReactElement } from "react";

import {
  sourcesByCitationCount,
  type SourceEntryMap,
} from "../../chatModel/sourcesReducer";
import { SourceRow } from "../citations/SourceRow";

export interface SourcesPanelProps {
  sources: SourceEntryMap;
  onClose: () => void;
}

export function SourcesPanel({
  sources,
  onClose,
}: SourcesPanelProps): ReactElement {
  const ordered = sourcesByCitationCount(sources);
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
          {ordered.map((source, index) => (
            <SourceRow
              key={`${source.source_connector}:${source.source_doc_id}`}
              source={source}
              ordinal={index + 1}
            />
          ))}
        </ul>
      )}
    </aside>
  );
}
