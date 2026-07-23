// Run receipt surface (Generative Surfaces v2, PRD-E1 / FR-E2). 🎨
//
// The run's accountability artifact, rendered from a `RunReceipt` — itself a
// pure fold of the Work Ledger (`projectReceipt`). Four stat tiles over a
// per-action row list, each row carrying its decision attribution, stable ledger
// id, and time. The footer states the two contract sentences verbatim, reuses
// B2's provenance footer (read-only access class), and offers "Copy receipt"
// (plain-text serialization via the host `onCopyText` — hashing export is E3).
//
// Pure presentational: no port/clock/browser reads; every action is a host
// callback. Kit-only styling (design-system recipes + tokens); no raw
// font-size / letter-spacing. Every string renders as text — a hostile row
// title can never inject markup.

import type { CSSProperties, ReactElement } from "react";

import { Badge, Caption, SectionLabel } from "@0x-copilot/design-system";
import type { ReceiptAttribution, RunReceipt } from "@0x-copilot/api-types";
import { formatLedgerId } from "@0x-copilot/api-types";

import { TcProvenanceFooter } from "../../thread-canvas/TcProvenanceFooter";
import type { SurfaceProvenance } from "../../thread-canvas/provenance";

export interface ReceiptSurfaceProps {
  readonly receipt: RunReceipt;
  /** The `receipt.emitted` sequence number — anchors the provenance ledger id. */
  readonly emittedSeq?: number | null;
  /** Host copy port (B2 reserved `onCopyText` for E1's "Copy receipt"). */
  readonly onCopyText?: (text: string) => void;
}

/** FR-E2/C8 attribution display labels (constant map — the only place wording
 *  lives). Rendered as plain text in a status chip. */
const ATTRIBUTION_LABELS: Record<ReceiptAttribution, string> = {
  auto_ran: "auto-ran",
  approved: "you approved",
  held: "you held",
  rejected: "you rejected",
  auto_applied: "auto-sent under allow-always",
  no_view_fit: "no view fit",
};

const ATTRIBUTION_TONES: Record<
  ReceiptAttribution,
  "neutral" | "success" | "warning" | "danger" | "accent" | "muted"
> = {
  auto_ran: "neutral",
  approved: "success",
  held: "warning",
  rejected: "danger",
  auto_applied: "accent",
  no_view_fit: "muted",
};

/** The two FR-E2 contract sentences — rendered verbatim in the footer. */
export const RECEIPT_DECIDED_ON_SURFACE_LINE =
  "Every write was decided on its surface — nothing was approved from chat.";
export const RECEIPT_ASSEMBLED_LINE =
  "Assembled from the run ledger · immutable.";

const TILES: ReadonlyArray<{
  readonly key: keyof RunReceipt["tiles"];
  readonly label: string;
}> = [
  { key: "reads_auto_ran", label: "Reads auto-ran" },
  { key: "writes_proposed", label: "Writes proposed" },
  { key: "writes_approved", label: "Writes approved" },
  { key: "holds_untouched", label: "Held, untouched" },
];

export function ReceiptSurface({
  receipt,
  emittedSeq = null,
  onCopyText,
}: ReceiptSurfaceProps): ReactElement {
  const provenance = receiptProvenance(receipt, emittedSeq);

  return (
    <div style={rootStyle} data-testid="receipt-surface">
      <div style={tileRowStyle} data-testid="receipt-tiles">
        {TILES.map((tile) => (
          <div key={tile.key} className="ui-card" style={tileStyle}>
            <span
              className="ui-stat"
              style={statStyle}
              data-testid={`receipt-tile-${tile.key}`}
            >
              {receipt.tiles[tile.key]}
            </span>
            <SectionLabel>{tile.label}</SectionLabel>
          </div>
        ))}
      </div>

      <ol style={rowsStyle} data-testid="receipt-rows">
        {receipt.rows.map((row, index) => (
          <li
            key={`${row.ledger_id}-${index}`}
            style={rowStyle}
            data-testid="receipt-row"
          >
            <Badge
              tone={ATTRIBUTION_TONES[row.attribution]}
              data-testid="receipt-row-attribution"
            >
              {ATTRIBUTION_LABELS[row.attribution]}
            </Badge>
            <span
              className="ui-item-title"
              style={titleStyle}
              data-testid="receipt-row-title"
            >
              {row.title}
            </span>
            <span style={spacerStyle} aria-hidden="true" />
            <Caption data-testid="receipt-row-time">{row.at}</Caption>
            <span className="ui-mono-caps" data-testid="receipt-row-ledger-id">
              {row.ledger_id}
            </span>
          </li>
        ))}
      </ol>

      <div style={contractStyle}>
        <Caption as="p" data-testid="receipt-decided-on-surface">
          {RECEIPT_DECIDED_ON_SURFACE_LINE}
        </Caption>
        <div style={assembledRowStyle}>
          <Caption as="p" data-testid="receipt-assembled">
            {RECEIPT_ASSEMBLED_LINE}
          </Caption>
          <span style={spacerStyle} aria-hidden="true" />
          {onCopyText !== undefined ? (
            <button
              type="button"
              className="ui-button"
              onClick={() => onCopyText(serializeReceipt(receipt))}
              data-testid="receipt-copy"
            >
              Copy receipt
            </button>
          ) : null}
        </div>
      </div>

      <TcProvenanceFooter provenance={provenance} />
    </div>
  );
}

/** Serialize the receipt rows + tiles as plain text (E3 owns the hashed export). */
export function serializeReceipt(receipt: RunReceipt): string {
  const lines: string[] = ["Run receipt"];
  for (const tile of TILES) {
    lines.push(`${tile.label}: ${receipt.tiles[tile.key]}`);
  }
  lines.push("");
  for (const row of receipt.rows) {
    lines.push(
      `${ATTRIBUTION_LABELS[row.attribution]} · ${row.title} · ${row.at} · ${row.ledger_id}`,
    );
  }
  lines.push("");
  lines.push(RECEIPT_DECIDED_ON_SURFACE_LINE);
  lines.push(RECEIPT_ASSEMBLED_LINE);
  return lines.join("\n");
}

function receiptProvenance(
  receipt: RunReceipt,
  emittedSeq: number | null,
): SurfaceProvenance {
  return {
    surfaceId: receipt.surface_id,
    ledgerId:
      emittedSeq !== null ? safeLedgerId(receipt.run_id, emittedSeq) : "",
    connector: "runtime",
    op: "receipt",
    kind: "receipt",
    latencyMs: null,
    accessClass: "read",
    tier: "pending",
    openIn: null,
  };
}

function safeLedgerId(runId: string, seq: number): string {
  try {
    return formatLedgerId(runId, seq);
  } catch {
    return `r${runId}·${seq}`;
  }
}

// ---------------------------------------------------------------------------
// Styles (design-system tokens only)
// ---------------------------------------------------------------------------

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-md)",
  minWidth: 0,
};

const tileRowStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
  gap: "var(--space-sm)",
  padding: "var(--space-md) var(--space-md) 0",
};

const tileStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-2xs, 2px)",
  padding: "var(--space-sm) var(--space-md)",
};

const statStyle: CSSProperties = {
  fontWeight: 600,
  fontVariantNumeric: "tabular-nums",
};

const rowsStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: "0 var(--space-md)",
  display: "flex",
  flexDirection: "column",
};

const rowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-sm)",
  padding: "6px 0",
  borderBottom: "1px solid var(--color-border-subtle)",
  minWidth: 0,
};

const titleStyle: CSSProperties = {
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  minWidth: 0,
};

const spacerStyle: CSSProperties = { flex: "1 1 auto" };

const contractStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-2xs, 2px)",
  padding: "0 var(--space-md)",
};

const assembledRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-sm)",
};
