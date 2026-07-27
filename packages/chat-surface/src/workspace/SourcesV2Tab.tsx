// Canonical E1 D5 Sources v2 rail body.
//
// Source facts are safe provenance, not dereference capabilities. This view
// deliberately renders controlled kind/sequence labels only; it does not put
// any opaque refs, paths, raw args, bodies, cookies, credentials, or provider
// tokens into the DOM. Artifact opening is delegated to the cockpit's one
// owner-routed facade call, keyed solely by source_id.

import type { ReactElement } from "react";

import { Caption } from "@0x-copilot/design-system";
import type { SourcesProjectionV2 } from "@0x-copilot/api-types";

import { Row, RowList } from "../destinations/_shared";
import {
  presentSourcesV2,
  type SourceRowPresentationV2,
} from "./sourcePresentationV2";

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
  const presentation = presentSourcesV2(sources);

  if (presentation.total === 0) {
    return (
      <div
        className="atlas-workspace-tab atlas-sources-panel atlas-sources-panel--empty"
        data-testid="sources-v2-empty"
      >
        <p className="atlas-sources-panel__empty">
          No sources yet — the run hasn't read anything.
        </p>
      </div>
    );
  }

  return (
    <div
      className="atlas-workspace-tab atlas-sources-panel"
      data-testid="sources-v2-tab"
    >
      <p className="atlas-sources-panel__note">
        Everything the agent read or fetched this run — the receipts behind each
        surface.
      </p>
      {openMessage !== null ? (
        <Caption
          as="p"
          className="atlas-sources-panel__status"
          data-testid="sources-v2-open-message"
          role="status"
        >
          {openMessage}
        </Caption>
      ) : null}
      {presentation.groups.map((group) => (
        <section
          key={group.key}
          className="atlas-sources-panel__group"
          aria-label={`${group.label} sources`}
          data-testid="sources-v2-group"
        >
          <div className="ui-mono-caps atlas-sources-panel__group-header">
            {group.label} · {group.rows.length}
          </div>
          <RowList
            items={group.rows}
            keyFor={(row) => row.id}
            ariaLabel={`${group.label} sources`}
            data-testid="sources-v2-list"
            renderRow={(row) => (
              <SourcePresentationRow
                row={row}
                opening={openingSourceId === row.id}
                onOpenSource={onOpenSource}
              />
            )}
          />
        </section>
      ))}
    </div>
  );
}

function SourcePresentationRow({
  row,
  opening,
  onOpenSource,
}: {
  readonly row: SourceRowPresentationV2;
  readonly opening: boolean;
  readonly onOpenSource?: (sourceId: string) => void;
}): ReactElement {
  const canOpen = row.openable && onOpenSource !== undefined && !opening;
  return (
    <Row
      data-testid="sources-v2-row"
      className="atlas-sources-panel__row"
      density="compact"
      icon={row.iconText}
      iconSize={30}
      iconVariant="identity"
      title={
        <span className="atlas-sources-panel__row-title">{row.title}</span>
      }
      sub={opening ? "Opening artifact…" : row.metadata}
      subFont="mono"
      trailing={row.openable ? <OpenSourceIcon /> : undefined}
      onActivate={
        canOpen && onOpenSource !== undefined
          ? () => onOpenSource(row.id)
          : undefined
      }
      ariaLabel={canOpen ? `Open ${row.title}` : undefined}
    />
  );
}

function OpenSourceIcon(): ReactElement {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      data-testid="sources-v2-open-artifact"
    >
      <path d="M14 4h6v6" />
      <path d="M20 4l-9 9" />
      <path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5" />
    </svg>
  );
}
