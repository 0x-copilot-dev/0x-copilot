// Canonical E1 D5 Sources v2 rail body.
//
// Source facts are safe provenance, not dereference capabilities. This view
// deliberately renders controlled kind/sequence labels only; it does not put
// any opaque refs, paths, raw args, bodies, cookies, credentials, or provider
// tokens into the DOM. Artifact opening is delegated to the cockpit's one
// owner-routed facade call, keyed solely by source_id.

import type { ReactElement, ReactNode } from "react";

import { Caption } from "@0x-copilot/design-system";
import type { SourcesProjectionV2 } from "@0x-copilot/api-types";

import { CompactSourceList, type CompactSourceItem } from "./CompactSourceList";
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
          aria-label="Cited documents"
          data-testid="sources-v2-citations"
        >
          {/* No group header here: the compact card draws its own eyebrow
              (`CITED · N`), and stacking a second "Cited" label above it read as
              a duplicated heading. Same for the fact groups below. */}
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
          aria-label={`${group.label} sources`}
          data-testid="sources-v2-group"
        >
          <CompactSourceList
            label={group.label}
            testId="sources-v2-list"
            rowTestId="sources-v2-row"
            items={group.rows.map((row) =>
              toCompactItem({
                row,
                opening: openingSourceId === row.id,
                onOpenSource,
              }),
            )}
          />
        </section>
      ))}
    </div>
  );
}

/** Normalise one safe ledger fact into a row of the shared compact list. */
function toCompactItem({
  row,
  opening,
  onOpenSource,
}: {
  readonly row: SourceRowPresentationV2;
  readonly opening: boolean;
  readonly onOpenSource?: (sourceId: string) => void;
}): CompactSourceItem {
  const canOpen = row.openable && onOpenSource !== undefined && !opening;
  return {
    id: row.id,
    ordinal: null,
    title: row.title,
    subtitle: opening ? "Opening artifact…" : row.metadata,
    // A fact carries no URL by design (`SourceFactV2` is opaque provenance —
    // "never authorization", nothing dereferenceable in the DOM), so it never
    // renders as a link. Opening is owner-routed through `onOpenSource`, keyed
    // solely by source_id. Do not "complete" the row by widening that contract.
    href: null,
    ...(canOpen && onOpenSource !== undefined
      ? { onActivate: () => onOpenSource(row.id) }
      : {}),
  };
}
