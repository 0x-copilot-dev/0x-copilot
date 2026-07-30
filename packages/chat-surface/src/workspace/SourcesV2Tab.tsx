// Canonical E1 D5 Sources v2 rail body.
//
// Source facts are safe provenance, not dereference capabilities. This view
// deliberately renders controlled kind/sequence labels only; it does not put
// any opaque refs, paths, raw args, bodies, cookies, credentials, or provider
// tokens into the DOM. Artifact opening is delegated to the cockpit's one
// owner-routed facade call, keyed solely by source_id.

import type { ReactElement, ReactNode } from "react";

import { Badge, Caption, Card } from "@0x-copilot/design-system";
import type { SourcesProjectionV2 } from "@0x-copilot/api-types";

import { RowList } from "../destinations/_shared";
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
  /**
   * Cited documents, injected as a node (the rail passes the legacy citation
   * `SourcesTab`).
   *
   * WHY A SLOT: a citation row carries a real title, URL, and snippet, and
   * `SourceFactV2` deliberately carries none of those — it is opaque provenance
   * ("never authorization", no refs/paths/bodies in the DOM). Widening that
   * contract to smuggle titles through would defeat its purpose, so the two
   * kinds of provenance are COMPOSED here instead: safe facts stay facts, and
   * citation rows arrive already-rendered by the component that owns them.
   * This tab therefore learns nothing about citation shapes.
   *
   * This exists because the v2 rail is the one actually mounted
   * (`isSurfacesV2Enabled()` defaults true) while `projectSourcesV2` folds only
   * ledger events — so a web search registered its sources correctly and the
   * user still saw an empty Sources panel.
   */
  readonly citationsSlot?: ReactNode;
}

export function SourcesV2Tab({
  sources,
  onOpenSource,
  openingSourceId = null,
  openMessage = null,
  citationsSlot,
}: SourcesV2TabProps): ReactElement {
  const presentation = presentSourcesV2(sources);

  // Only the ledger fold can be empty while citations exist (a web search
  // registers sources but emits no `read.executed`), so the empty state must
  // consider BOTH — otherwise the cited-documents section is unreachable.
  if (presentation.total === 0 && citationsSlot === undefined) {
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
      {/* Cited documents lead: they are what the reader clicked a `[[N]]` chip
          to reach, whereas the ledger facts below are the provenance trail. */}
      {citationsSlot !== undefined ? (
        <section
          className="atlas-sources-panel__group"
          aria-label="Cited documents"
          data-testid="sources-v2-citations"
        >
          <div className="ui-mono-caps atlas-sources-panel__group-header">
            Cited
          </div>
          {citationsSlot}
        </section>
      ) : null}
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
  // The SAME card the cited-documents rows use (`.atlas-source-row` + design-
  // system `Card`), so one Sources rail does not present two different row
  // languages — a compact list row for facts and a rich card for citations.
  //
  // What it CANNOT show is the citation card's link and snippet: a fact carries
  // no URL, title, or body by design (`SourceFactV2` is opaque provenance —
  // "never authorization", nothing dereferenceable in the DOM). So the card
  // shape is shared and the fields degrade honestly: glyph + safe title +
  // connector badge, with the metadata line where the snippet would be. Do not
  // "complete" this card by widening the fact contract.
  return (
    <li
      data-testid="sources-v2-row"
      data-source-id={row.id}
      className="atlas-source-row"
    >
      <Card tone="default">
        <div className="atlas-source-row__top">
          <button
            type="button"
            className="atlas-source-row__head"
            onClick={
              canOpen && onOpenSource !== undefined
                ? () => onOpenSource(row.id)
                : undefined
            }
            disabled={!canOpen}
            aria-label={canOpen ? `Open ${row.title}` : row.title}
          >
            <span
              className="atlas-source-row__glyph-trigger"
              aria-hidden="true"
            >
              <span className="atlas-source-row__glyph">{row.iconText}</span>
            </span>
            <span className="atlas-source-row__title">{row.title}</span>
            {row.openable ? (
              <Badge tone="neutral">
                <OpenSourceIcon />
              </Badge>
            ) : null}
          </button>
        </div>
        <p className="atlas-source-row__footnote">
          {opening ? "Opening artifact…" : row.metadata}
        </p>
      </Card>
    </li>
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
