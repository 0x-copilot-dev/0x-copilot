/**
 * Discriminated host for the slash-command side panels (`/context`, `/usage`,
 * `/sources`).
 *
 * The shell — overlay + close button + state plumbing — is the same for
 * every panel; the body switches on `kind`. New panels register here.
 */

import type { ReactElement } from "react";
import type { RequestIdentity } from "../../../../api/config";
import type { CitationLookup } from "../citations/citationsContext";
import { ContextPanel } from "./ContextPanel";
import { SourcesPanel } from "./SourcesPanel";
import { UsagePanel } from "./UsagePanel";

export type DetailsPanelKind = "context" | "usage" | "sources";

export interface DetailsPanelHostProps {
  kind: DetailsPanelKind;
  conversationId: string | null;
  identity: RequestIdentity;
  citations: CitationLookup;
  onClose: () => void;
}

export function DetailsPanelHost({
  kind,
  conversationId,
  identity,
  citations,
  onClose,
}: DetailsPanelHostProps): ReactElement | null {
  if (kind === "sources") {
    return <SourcesPanel citations={citations} onClose={onClose} />;
  }
  if (kind === "context") {
    if (conversationId === null) {
      return (
        <aside className="details-panel" data-testid="context-panel-empty">
          <p className="details-panel__empty">
            Open a conversation first, then run `/context` to see token usage.
          </p>
        </aside>
      );
    }
    return (
      <ContextPanel
        conversationId={conversationId}
        identity={identity}
        onClose={onClose}
      />
    );
  }
  return <UsagePanel identity={identity} onClose={onClose} />;
}
