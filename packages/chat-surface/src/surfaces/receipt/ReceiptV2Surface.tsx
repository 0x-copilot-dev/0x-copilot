// Canonical E1 D4 receipt presentation.
//
// This component intentionally accepts the existing `RunReceiptV2` fold rather
// than independently recounting ledger events. It is presentational only: no
// transport, clipboard, filesystem, clock, or browser access. The Studio host
// decides whether an explicit user action turns it into a canvas tab.

import type { ReactElement, ReactNode } from "react";

import { Badge, Caption, SectionLabel } from "@0x-copilot/design-system";
import type { RunReceiptV2 } from "@0x-copilot/api-types";

export interface ReceiptV2SurfaceProps {
  readonly receipt: RunReceiptV2;
  /** Present only when Studio is enabled by the product-level host flag. */
  readonly onOpenInStudio?: () => void;
}

/** The canonical v2 receipt, safe for both a Focus card and a Studio tab. */
export function ReceiptV2Surface({
  receipt,
  onOpenInStudio,
}: ReceiptV2SurfaceProps): ReactElement {
  return (
    <section className="ui-card" data-testid="receipt-v2-surface">
      <header>
        <SectionLabel>Run receipt</SectionLabel>
        <Badge tone="neutral" data-testid="receipt-v2-status">
          {statusLabel(receipt.status)}
        </Badge>
      </header>

      <ReceiptMetric label="Operations">
        {receipt.operations.requested} requested ·{" "}
        {receipt.operations.completed} completed · {receipt.operations.failed}{" "}
        failed
      </ReceiptMetric>
      <ReceiptMetric label="Reads">
        {receipt.reads.completed} completed
      </ReceiptMetric>
      <ReceiptMetric label="Artifacts">
        {receipt.artifacts.created} created · {receipt.artifacts.revised}{" "}
        revised · {receipt.artifacts.promoted} promoted
      </ReceiptMetric>
      <ReceiptMetric label="Effects">
        {receipt.effects.proposed} proposed · {receipt.effects.approved}{" "}
        approved · {receipt.effects.applied} applied ·{" "}
        {receipt.effects.rejected} rejected
      </ReceiptMetric>
      <ReceiptMetric label="Access gates">
        {receipt.gates.opened} opened · {receipt.gates.resolved} resolved ·{" "}
        {receipt.gates.pending} pending
      </ReceiptMetric>
      <ReceiptMetric label="Model usage">
        {receipt.usage.totals_by_purpose.reduce(
          (total, item) => total + item.records,
          0,
        )}{" "}
        recorded calls
      </ReceiptMetric>

      {receipt.unresolved_warnings.length > 0 ? (
        <Caption as="p" data-testid="receipt-v2-warning">
          Some ledger entries could not be included in this receipt.
        </Caption>
      ) : null}
      {onOpenInStudio !== undefined ? (
        <button
          type="button"
          className="ui-button ui-button--ghost"
          onClick={onOpenInStudio}
          data-testid="receipt-v2-open-studio"
        >
          Open in Studio
        </button>
      ) : null}
    </section>
  );
}

/** A compact, explicit Studio affordance. It never opens itself. */
export function ReceiptV2LaunchCard(props: {
  readonly receipt: RunReceiptV2;
  readonly onOpen: () => void;
}): ReactElement {
  return (
    <section className="ui-card ui-card--muted" data-testid="receipt-v2-launch">
      <SectionLabel>Run receipt ready</SectionLabel>
      <Badge tone="neutral">{statusLabel(props.receipt.status)}</Badge>
      <Caption as="p">
        This receipt was assembled from the run ledger. It opens only when you
        choose to review it.
      </Caption>
      <button
        type="button"
        className="ui-button ui-button--ghost"
        onClick={props.onOpen}
        data-testid="receipt-v2-open"
      >
        Open receipt
      </button>
    </section>
  );
}

function ReceiptMetric(props: {
  readonly label: string;
  readonly children: ReactNode;
}): ReactElement {
  return (
    <div data-testid="receipt-v2-metric">
      <span className="ui-section-label">{props.label}</span>
      <span className="ui-item-title">{props.children}</span>
    </div>
  );
}

function statusLabel(status: RunReceiptV2["status"]): string {
  switch (status) {
    case "completed":
      return "Completed";
    case "failed":
      return "Failed";
    case "cancelled":
      return "Cancelled";
    case "timed_out":
      return "Timed out";
    case "blocked":
      return "Blocked";
    case "indeterminate":
      return "Needs review";
    default:
      return "In progress";
  }
}
