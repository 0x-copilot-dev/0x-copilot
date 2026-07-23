// Ledger Sources tab body (Generative Surfaces v2, PRD-E1 / FR-E3). 🎨
//
// The Sources rail for a v2 run: everything read this run, grouped by connector,
// rendered directly from a `LedgerSourcesProjection` (the pure `projectLedgerSources`
// fold over the run stream). Each row carries op · time · latency · the
// "auto-ran (read)" qualifier · stable ledger id. Presentational only — no
// port/clock/browser reads. Reuses the `atlas-workspace-tab` chrome so it sits
// beside the legacy `SourcesTab` unchanged.

import type { ReactElement } from "react";

import { Badge, Caption } from "@0x-copilot/design-system";

import { humanizeConnector } from "../citations/connectorLabel";
import { formatLatency } from "../thread-canvas/provenance";
import type { LedgerSourcesProjection } from "../destinations/run/projectLedgerSources";

export interface LedgerSourcesTabProps {
  readonly ledgerSources: LedgerSourcesProjection;
}

export function LedgerSourcesTab({
  ledgerSources,
}: LedgerSourcesTabProps): ReactElement {
  if (ledgerSources.total === 0) {
    return (
      <div
        className="atlas-workspace-tab atlas-workspace-tab--empty"
        data-testid="ledger-sources-empty"
      >
        <p>Sources will appear here as the run reads your tools.</p>
      </div>
    );
  }

  return (
    <div className="atlas-workspace-tab" data-testid="ledger-sources-tab">
      {ledgerSources.groups.map((group) => (
        <section
          key={group.connector}
          className="atlas-workspace-tab__group"
          aria-label={`${humanizeConnector(group.connector)} sources`}
        >
          <header className="atlas-workspace-tab__group-header ui-section-label">
            <span data-testid="ledger-sources-group">
              {humanizeConnector(group.connector)}
            </span>
            <span className="atlas-workspace-tab__group-count">
              {group.rows.length}
            </span>
          </header>
          <ul className="atlas-workspace-tab__list" aria-live="polite">
            {group.rows.map((row, index) => (
              <li
                key={`${row.ledgerId}-${index}`}
                style={rowStyle}
                data-testid="ledger-sources-row"
              >
                <span className="ui-item-title" style={titleStyle}>
                  {row.title}
                </span>
                <span style={metaStyle}>
                  <Caption data-testid="ledger-sources-op">{row.op}</Caption>
                  <Caption data-testid="ledger-sources-time">{row.at}</Caption>
                  {row.latencyMs !== null ? (
                    <Caption data-testid="ledger-sources-latency">
                      {formatLatency(row.latencyMs)}
                    </Caption>
                  ) : null}
                  <Badge tone="neutral" data-testid="ledger-sources-qualifier">
                    {row.qualifier}
                  </Badge>
                  <span
                    className="ui-mono-caps"
                    data-testid="ledger-sources-ledger-id"
                  >
                    {row.ledgerId}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

const rowStyle = {
  display: "flex",
  flexDirection: "column" as const,
  gap: "var(--space-2xs, 2px)",
  padding: "6px 0",
  borderBottom: "1px solid var(--color-border-subtle)",
  minWidth: 0,
};

const titleStyle = {
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap" as const,
  minWidth: 0,
};

const metaStyle = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-sm)",
  flexWrap: "wrap" as const,
};
