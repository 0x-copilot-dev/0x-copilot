// PendingCardList — the Approvals rail's cross-run pending queue (PRD-E2 / FR-E5). 🎨
//
// One COMPACT card per pending item — a parked gate, a held single-artifact
// draft, or a staged row-set — each with a "Review →" action that flips the
// canvas to the owning surface (host-routed). Cards accumulate lazily as the run
// discovers them (FR-B2). Pure presentational: it owns no fetch and no
// projection — the host threads the merged `cards` (live open-run ⊕ fetched
// cross-run) and the `onReview` router.
//
// Kit-only styling: `.ui-eyebrow` (kind), `.ui-badge` (connector, via <Badge>),
// `.ui-pill` (row count), `.ui-mono-caps` (ledger id), `.ui-button--sm`
// (Review). Titles/purposes are UNTRUSTED ledger text — rendered as text nodes
// only, NEVER `dangerouslySetInnerHTML`.
//
// Boundary: framework-agnostic — no bare window/document/fetch; design-system
// tokens only.

import { Badge } from "@0x-copilot/design-system";
import type { CSSProperties, ReactElement } from "react";

import type { PendingCard } from "../destinations/run/pendingCardsProjection";

export interface PendingCardListProps {
  readonly cards: readonly PendingCard[];
  readonly onReview: (card: PendingCard) => void;
  /** Empty-state copy; a sensible default when the queue is clear. */
  readonly emptyCopy?: string;
}

const DEFAULT_EMPTY = "Nothing waiting on you.";

export function PendingCardList({
  cards,
  onReview,
  emptyCopy = DEFAULT_EMPTY,
}: PendingCardListProps): ReactElement {
  if (cards.length === 0) {
    return (
      <div data-testid="pending-card-list-empty" style={emptyStyle}>
        {emptyCopy}
      </div>
    );
  }
  return (
    <ul
      data-testid="pending-card-list"
      aria-label="Pending work"
      style={listStyle}
    >
      {cards.map((card) => (
        <li
          key={cardKey(card)}
          data-item-kind={card.itemKind}
          style={cardStyle}
        >
          <div style={headRowStyle}>
            <span className="ui-eyebrow" data-testid="pending-card-kind">
              {kindLabel(card)}
            </span>
            <Badge tone="neutral" data-testid="pending-card-connector">
              {card.connector}
            </Badge>
          </div>
          <div style={titleStyle} data-testid="pending-card-title">
            {card.title}
          </div>
          {rowCountLabel(card) !== null ? (
            <span
              className="ui-pill"
              data-testid="pending-card-rows"
              style={pillStyle}
            >
              {rowCountLabel(card)}
            </span>
          ) : null}
          <div style={footRowStyle}>
            <span
              className="ui-mono-caps ui-mono-caps--9"
              data-testid="pending-card-ledger-id"
            >
              {card.ledgerId}
            </span>
            <button
              type="button"
              className="ui-button ui-button--sm"
              data-testid="pending-card-review"
              onClick={() => onReview(card)}
              aria-label={`Review "${card.title}"`}
            >
              Review →
            </button>
          </div>
        </li>
      ))}
    </ul>
  );
}

// ── labels ─────────────────────────────────────────────────────────────────

function kindLabel(card: PendingCard): string {
  if (card.itemKind === "gate") return "GATE";
  // A row-set stage carries row counts; a single-artifact draft does not.
  return card.rowsTotal !== null ? "STAGED CHANGES" : "HELD DRAFT";
}

/** "5 of 8 waiting" for a row-set with pending rows; null otherwise. */
function rowCountLabel(card: PendingCard): string | null {
  if (card.rowsTotal === null || card.rowsPending === null) return null;
  return `${card.rowsPending} of ${card.rowsTotal} waiting`;
}

function cardKey(card: PendingCard): string {
  return `${card.runId}::${card.gateId ?? card.stageId ?? card.ledgerId}`;
}

// ── styles (layout only; type/color come from kit recipes + tokens) ──────────

const listStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: "var(--space-sm, 8px)",
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-sm, 8px)",
};

const cardStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-2xs, 4px)",
  padding: "var(--space-sm, 8px) var(--space-md, 12px)",
  border: "1px solid var(--color-border, #22252e)",
  borderRadius: "var(--radius-md, 8px)",
  background: "var(--color-bg-surface, #1b1d24)",
};

const headRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: "var(--space-sm, 8px)",
};

const titleStyle: CSSProperties = {
  color: "var(--color-text, #f4f5f6)",
  wordBreak: "break-word",
};

const footRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: "var(--space-sm, 8px)",
  marginTop: "var(--space-2xs, 4px)",
};

const pillStyle: CSSProperties = {
  alignSelf: "flex-start",
};

const emptyStyle: CSSProperties = {
  padding: "var(--space-lg, 16px)",
  color: "var(--color-text-muted, #9aa0aa)",
};
