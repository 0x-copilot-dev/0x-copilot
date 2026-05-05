// PR 3.1 — Sources tab body for the right-rail Workspace pane (PR 3.2).
//
// Pure presentational: receives the same SourceEntryMap that powers the
// slash-command `/sources` overlay (PR 1.5 reducer + PR 3.1 archive seed).
// PR 3.2's <WorkspacePane /> mounts this tab; this PR ships the body so
// the data path can be exercised end-to-end before the pane host lands.
//
// Behavior:
//   - Empty state when no sources have been ingested.
//   - One row per unique source via the shared <SourceRow /> primitive.
//   - `focusCitationId` scrolls the matching row into view (chip-click
//     handshake from PR 3.1 §2.5 / `useWorkspacePaneAutoOpen`).

import type { SourceEntry } from "@enterprise-search/api-types";
import { useEffect, useRef, type ReactElement } from "react";

import {
  sourcesByCitationCount,
  type SourceEntryMap,
} from "../../chatModel/sourcesReducer";
import { SourceRow } from "../citations/SourceRow";

export interface SourcesTabProps {
  sources: SourceEntryMap;
  loading?: boolean;
  error?: string | null;
  /** Citation id to scroll into focus on next render. */
  focusCitationId?: string | null;
  onSelect?: (source: SourceEntry) => void;
}

export function SourcesTab({
  sources,
  loading,
  error,
  focusCitationId,
  onSelect,
}: SourcesTabProps): ReactElement {
  const ordered = sourcesByCitationCount(sources);
  const focusRef = useRef<HTMLLIElement | null>(null);

  useEffect(() => {
    if (focusCitationId && focusRef.current) {
      focusRef.current.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [focusCitationId, ordered.length]);

  if (ordered.length === 0) {
    return (
      <div className="atlas-workspace-tab atlas-workspace-tab--empty">
        {loading ? (
          <p>Loading sources…</p>
        ) : error ? (
          <p role="alert">Couldn’t load sources — {error}</p>
        ) : (
          <p>Sources will appear here as Atlas finds them.</p>
        )}
      </div>
    );
  }

  return (
    <div className="atlas-workspace-tab" data-testid="workspace-sources-tab">
      {error ? (
        <p
          className="atlas-workspace-tab__stale"
          role="status"
          data-testid="workspace-sources-tab-stale"
        >
          Showing live results — older history failed to load ({error}).
        </p>
      ) : null}
      <ul
        className="atlas-workspace-tab__list"
        aria-live="polite"
        aria-label="Sources cited in this conversation"
      >
        {ordered.map((source, index) => {
          const isFocused = source.citation_id === focusCitationId;
          return (
            <SourceRow
              key={`${source.source_connector}:${source.source_doc_id}`}
              ref={isFocused ? focusRef : undefined}
              source={source}
              ordinal={index + 1}
              focused={isFocused}
              onSelect={onSelect}
            />
          );
        })}
      </ul>
    </div>
  );
}
