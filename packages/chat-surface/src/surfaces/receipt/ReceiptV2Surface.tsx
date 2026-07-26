// Canonical E1 D4 receipt presentation.
//
// This component intentionally accepts the existing `RunReceiptV2` fold rather
// than independently recounting ledger events. It is presentational only: no
// transport, clipboard, filesystem, clock, or browser access. The Studio host
// decides whether an explicit user action turns it into a canvas tab.

import type { CSSProperties, ReactElement, ReactNode } from "react";

import { Badge, Caption, SectionLabel } from "@0x-copilot/design-system";
import type { RunReceiptV2 } from "@0x-copilot/api-types";

export interface ReceiptV2SurfaceProps {
  readonly receipt: RunReceiptV2;
}

/** The canonical v2 receipt surface, rendered only in Studio. */
export function ReceiptV2Surface({
  receipt,
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
    </section>
  );
}

/**
 * A deliberately quiet receipt affordance. Receipts are audit material, not a
 * second primary conversation surface: the cockpit only mounts this control
 * for consequential work and never turns it into a large explanatory card.
 */
export function ReceiptV2LaunchCard(props: {
  readonly receipt: RunReceiptV2;
  readonly onOpen: () => void;
}): ReactElement {
  return (
    <section data-testid="receipt-v2-launch" style={launchStyle}>
      <span style={launchLabelStyle}>Run receipt</span>
      <Badge tone="neutral">{statusLabel(props.receipt.status)}</Badge>
      <button
        type="button"
        className="ui-button ui-button--ghost"
        onClick={props.onOpen}
        data-testid="receipt-v2-open"
      >
        Review
      </button>
    </section>
  );
}

const launchStyle: CSSProperties = {
  alignItems: "center",
  alignSelf: "flex-start",
  border: "1px solid var(--color-border-subtle)",
  borderRadius: "var(--radius-md)",
  display: "flex",
  gap: "var(--space-xs)",
  padding: "var(--space-2xs) var(--space-xs)",
};

const launchLabelStyle: CSSProperties = {
  color: "var(--color-text-muted)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-2xs)",
  fontWeight: 600,
  letterSpacing: "var(--tracking-label)",
  textTransform: "uppercase",
};

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
