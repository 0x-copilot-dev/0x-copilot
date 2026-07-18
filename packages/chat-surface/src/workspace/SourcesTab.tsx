// PR 3.1 — Sources tab body for the right-rail Workspace pane (PR 3.2).
// PR-1.7 — hoisted into @0x-copilot/chat-surface behind the SourceRow slot.
//
// Pure presentational: receives the same SourceEntryMap that powers the
// slash-command `/sources` overlay (PR 1.5 reducer + PR 3.1 archive seed).
//
// Two host-substrate concerns are injected, keeping this body free of the
// browser preview portal (FR-1.14):
//   - Row rendering — the `SourceRowComponent` slot defaults to the shared
//     headless `SourceRow`. The web host passes its preview-wired wrapper
//     (which binds `useSourcePreviewTrigger`); desktop can pass one that
//     omits the preview.
//
// Behavior:
//   - Empty state when no sources have been ingested.
//   - One row per unique source via the shared `SourceRow` primitive.
//   - `focusCitationId` scrolls the matching row into view (chip-click
//     handshake from PR 3.1 §2.5 / `useWorkspacePaneAutoOpen`).

import type { SourceEntry } from "@0x-copilot/api-types";
import {
  useEffect,
  useRef,
  type ForwardRefExoticComponent,
  type ReactElement,
  type RefAttributes,
} from "react";

import { humanizeConnector } from "../citations/connectorLabel";
import { SourceSkeletonRow } from "../citations/SourceSkeletonRow";
import { SourceRow, type SourceRowProps } from "../citations/SourceRow";
import {
  groupSourcesByConnector,
  sourcesByCitationCount,
  type SourceConnectorGroup,
  type SourceEntryMap,
} from "./workspaceHelpers";

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
  /**
   * Row renderer. Defaults to the shared headless `SourceRow`. The web host
   * passes its preview-wired `SourceRow` wrapper so the hover-preview portal
   * stays in the host substrate. Typed as a forwardRef component because the
   * tab forwards a `ref` to the focused row for scroll-into-view.
   */
  SourceRowComponent?: SourceRowSlot;
}

/** A `SourceRow`-shaped component that forwards a ref to its `<li>`. */
export type SourceRowSlot = ForwardRefExoticComponent<
  SourceRowProps & RefAttributes<HTMLLIElement>
>;

export function SourcesTab({
  sources,
  loading,
  error,
  focusCitationId,
  searching,
  searchingLabel,
  onSelect,
  onJumpToChat,
  SourceRowComponent = SourceRow,
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
          <p>Sources will appear here as Copilot finds them.</p>
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
              <SourceRowComponent
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
                  <SourceRowComponent
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
