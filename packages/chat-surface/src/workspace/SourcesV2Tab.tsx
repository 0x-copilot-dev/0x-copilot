// Canonical E1 D5 Sources v2 rail body.
//
// Source facts are safe provenance, not dereference capabilities. This view
// deliberately renders controlled kind/sequence labels only; it does not put
// any opaque refs, paths, raw args, bodies, cookies, credentials, or provider
// tokens into the DOM. Artifact opening is delegated to the cockpit's one
// owner-routed facade call, keyed solely by source_id.

import type { ReactElement } from "react";

import { Badge, Caption, SectionLabel } from "@0x-copilot/design-system";
import type {
  SourceFactKindV2,
  SourcesProjectionV2,
} from "@0x-copilot/api-types";

export interface SourcesV2TabProps {
  readonly sources: SourcesProjectionV2;
  readonly onOpenSource?: (sourceId: string) => void;
  readonly openingSourceId?: string | null;
  /** A host-controlled generic outcome line; never render server internals. */
  readonly openMessage?: string | null;
}

export function SourcesV2Tab({
  sources,
  onOpenSource,
  openingSourceId = null,
  openMessage = null,
}: SourcesV2TabProps): ReactElement {
  if (sources.facts.length === 0) {
    return (
      <div
        className="atlas-workspace-tab atlas-workspace-tab--empty"
        data-testid="sources-v2-empty"
      >
        <p>Sources will appear here as the run records provenance.</p>
      </div>
    );
  }

  return (
    <div className="atlas-workspace-tab" data-testid="sources-v2-tab">
      <header>
        <SectionLabel>Run sources</SectionLabel>
        <Badge tone="neutral">{sources.facts.length}</Badge>
      </header>
      {openMessage !== null ? (
        <Caption as="p" data-testid="sources-v2-open-message" role="status">
          {openMessage}
        </Caption>
      ) : null}
      <ul aria-live="polite">
        {sources.facts.map((fact) => {
          const canOpen =
            fact.kind === "artifact" &&
            fact.artifact_id !== null &&
            fact.artifact_revision !== null &&
            onOpenSource !== undefined;
          const opening = openingSourceId === fact.source_id;
          return (
            <li key={fact.source_id} data-testid="sources-v2-row">
              <span className="ui-item-title">{labelFor(fact.kind)}</span>
              <Caption>Recorded at step {fact.sequence_no}</Caption>
              {canOpen ? (
                <button
                  type="button"
                  className="ui-button ui-button--ghost"
                  disabled={opening}
                  onClick={() => onOpenSource(fact.source_id)}
                  data-testid="sources-v2-open-artifact"
                >
                  {opening ? "Opening…" : "Open artifact"}
                </button>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function labelFor(kind: SourceFactKindV2): string {
  switch (kind) {
    case "connector":
      return "Connector activity";
    case "artifact":
      return "Artifact";
    case "workspace":
      return "Workspace activity";
    case "browser":
      return "Browser activity";
    case "sandbox":
      return "Sandbox activity";
    case "subagent":
      return "Subagent activity";
    case "external_receipt":
      return "External receipt";
  }
}
