// Sources panel — presentational body for the slash-command (`/sources`)
// overlay and (later) the right-rail Sources tab.
//
// PR-1.4 — hoisted into @0x-copilot/chat-surface. Headless: it renders one
// `SourceRow` per already-ordered source and owns no data-binding. Two
// concerns stay host-side and are injected:
//
//   1. Ordering — the host applies its `sourcesByCitationCount` reducer
//      (chatModel, app-owned) and passes the ordered array in.
//   2. Row rendering — the `SourceRowComponent` slot defaults to the shared
//      headless `SourceRow`. The web host passes its preview-wired wrapper
//      (which binds `useSourcePreviewTrigger`, a browser-portal adapter that
//      must stay in the host). Desktop can pass a wrapper that omits the
//      preview or routes through its own mechanism.
//
// Keeping both host-side leaves this file free of the host reducers and the
// browser preview portal, so it stays substrate-agnostic.

import type { SourceEntry } from "@0x-copilot/api-types";
import { Button } from "@0x-copilot/design-system";
import type { ComponentType, ReactElement } from "react";

import { SourceRow, type SourceRowProps } from "./SourceRow";

export interface SourcesPanelProps {
  /**
   * Sources for this conversation, already ordered by the host (web:
   * `sourcesByCitationCount` — citation_count desc, then last_cited_at desc).
   */
  sources: readonly SourceEntry[];
  onClose: () => void;
  /**
   * Row renderer. Defaults to the shared headless `SourceRow`. The web host
   * passes its preview-wired `SourceRow` wrapper so the hover-preview portal
   * stays in the host substrate.
   */
  SourceRowComponent?: ComponentType<SourceRowProps>;
}

export function SourcesPanel({
  sources,
  onClose,
  SourceRowComponent = SourceRow,
}: SourcesPanelProps): ReactElement {
  return (
    <aside className="details-panel" data-testid="sources-panel">
      <header className="details-panel__header">
        <div>
          <h2>Sources</h2>
          <p className="details-panel__subtitle">
            {sources.length === 0
              ? "Sources will appear here as Copilot finds them."
              : `${sources.length} source${sources.length === 1 ? "" : "s"} cited.`}
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
      {sources.length === 0 ? (
        <p className="details-panel__empty">
          No citations yet. Start a turn that touches a connector.
        </p>
      ) : (
        <ul className="details-panel__list" data-testid="sources-panel-list">
          {sources.map((source, index) => (
            <SourceRowComponent
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
