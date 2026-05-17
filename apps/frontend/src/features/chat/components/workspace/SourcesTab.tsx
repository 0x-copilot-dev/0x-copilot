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
  groupSourcesByConnector,
  sourcesByCitationCount,
  type SourceConnectorGroup,
  type SourceEntryMap,
} from "../../chatModel/sourcesReducer";
import {
  humanizeConnector,
  SourceSkeletonRow,
} from "@enterprise-search/chat-surface";
import { SourceRow } from "../citations/SourceRow";

const GROUP_THRESHOLD = 5;

export interface SourcesTabProps {
  sources: SourceEntryMap;
  loading?: boolean;
  error?: string | null;
  /** Citation id to scroll into focus on next render. */
  focusCitationId?: string | null;
  /**
   * PR 3.7.1 — when true, a source-producing tool call is in flight.
   * Drives the "Looking for sources…" shimmer row that fills the empty
   * state so the user sees the panel is alive while results stream in.
   */
  searching?: boolean;
  /** Override label for the shimmer; defaults to "Looking for sources…". */
  searchingLabel?: string;
  onSelect?: (source: SourceEntry) => void;
  /** PR 1.1-rev2 — invoked when the per-row ↗ jump button is clicked.
   *  The host scrolls the chat viewport to the first chip that cited
   *  this source. Optional — when omitted the button is hidden. */
  onJumpToChat?: (source: SourceEntry) => void;
}

export function SourcesTab({
  sources,
  loading,
  error,
  focusCitationId,
  searching,
  searchingLabel,
  onSelect,
  onJumpToChat,
}: SourcesTabProps): ReactElement {
  const ordered = sourcesByCitationCount(sources);
  const focusRef = useRef<HTMLLIElement | null>(null);

  useEffect(() => {
    if (focusCitationId && focusRef.current) {
      focusRef.current.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [focusCitationId, ordered.length]);

  if (ordered.length === 0) {
    if (searching) {
      return (
        <div className="atlas-workspace-tab">
          <SourceSkeletonRow label={searchingLabel ?? "Looking for sources…"} />
        </div>
      );
    }
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

  // Ordinals are global (per the citation_count sort), so chip ↔ row
  // numbering stays stable whether we render flat or grouped.
  const ordinalById = new Map<string, number>();
  ordered.forEach((source, index) => {
    ordinalById.set(source.citation_id, index + 1);
  });

  const groups: readonly SourceConnectorGroup[] | null =
    ordered.length >= GROUP_THRESHOLD ? groupSourcesByConnector(ordered) : null;

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
      {groups === null ? (
        <ul
          className="atlas-workspace-tab__list"
          aria-live="polite"
          aria-label="Sources cited in this conversation"
        >
          {ordered.map((source) => {
            const isFocused = source.citation_id === focusCitationId;
            return (
              <SourceRow
                key={`${source.source_connector}:${source.source_doc_id}`}
                ref={isFocused ? focusRef : undefined}
                source={source}
                ordinal={ordinalById.get(source.citation_id) ?? 0}
                focused={isFocused}
                onSelect={onSelect}
                onJumpToChat={onJumpToChat}
              />
            );
          })}
        </ul>
      ) : (
        groups.map((group) => (
          <section
            key={group.connector}
            className="atlas-workspace-tab__group"
            aria-label={`${humanizeConnector(group.connector)} sources`}
          >
            <header className="atlas-workspace-tab__group-header">
              <span>{humanizeConnector(group.connector)}</span>
              <span className="atlas-workspace-tab__group-count">
                {group.total}
              </span>
            </header>
            <ul className="atlas-workspace-tab__list" aria-live="polite">
              {group.rows.map((source) => {
                const isFocused = source.citation_id === focusCitationId;
                return (
                  <SourceRow
                    key={`${source.source_connector}:${source.source_doc_id}`}
                    ref={isFocused ? focusRef : undefined}
                    source={source}
                    ordinal={ordinalById.get(source.citation_id) ?? 0}
                    focused={isFocused}
                    onSelect={onSelect}
                  />
                );
              })}
            </ul>
          </section>
        ))
      )}
    </div>
  );
}
