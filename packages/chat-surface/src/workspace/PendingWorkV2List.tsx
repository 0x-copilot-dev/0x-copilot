// PendingWorkV2List — canonical v2.1 pending work in the Studio Approvals rail.
//
// Unlike the legacy pending queue, the v2.1 endpoint intentionally carries no
// title, connector, path, reason, target, reference, or content. This list
// therefore renders only controlled enum labels. The exact opaque target stays
// in the callback closure so Review can route to the owning run/Studio subject
// without ever displaying or interpolating it.

import type { CSSProperties, ReactElement } from "react";

import type { PendingWorkCardV2 } from "../destinations/run/pendingWorkV2Projection";
import {
  pendingWorkCardV2Key,
  pendingWorkStatusLabelV2,
  pendingWorkSubjectLabelV2,
} from "../destinations/run/pendingWorkV2Projection";

export interface PendingWorkV2ListProps {
  readonly cards: readonly PendingWorkCardV2[];
  readonly loading: boolean;
  /** The server omitted at least one authorised run; render safe copy only. */
  readonly partial: boolean;
  /** A refresh failed after verified cards had already been received. */
  readonly stale: boolean;
  readonly hasMore: boolean;
  readonly onReview: (card: PendingWorkCardV2) => void;
  readonly onLoadMore: () => void;
}

export function PendingWorkV2List({
  cards,
  loading,
  partial,
  stale,
  hasMore,
  onReview,
  onLoadMore,
}: PendingWorkV2ListProps): ReactElement | null {
  // The host only constructs this prop after either verified work or an
  // explicit partial-result marker. Keep standalone callers equally honest.
  if (cards.length === 0 && !partial && !stale) return null;

  return (
    <section
      aria-label="Canonical pending work"
      data-testid="pending-work-v2-list"
      style={sectionStyle}
    >
      <div style={headingStyle}>
        <span className="ui-eyebrow">RUNTIME WORK</span>
        {cards.length > 0 ? (
          <span className="ui-pill" data-testid="pending-work-v2-count">
            {cards.length} waiting
          </span>
        ) : null}
      </div>
      {cards.length > 0 ? (
        <ul style={listStyle}>
          {cards.map((card) => (
            <li key={pendingWorkCardV2Key(card)} style={cardStyle}>
              <div style={cardHeadStyle}>
                <span className="ui-eyebrow" data-testid="pending-work-v2-kind">
                  {pendingWorkSubjectLabelV2(card.subjectKind)}
                </span>
                <span className="ui-pill" data-testid="pending-work-v2-status">
                  {pendingWorkStatusLabelV2(card.status)}
                </span>
              </div>
              <p style={copyStyle}>Open Studio to review this runtime item.</p>
              <div style={footerStyle}>
                <button
                  type="button"
                  className="ui-button ui-button--sm"
                  data-testid="pending-work-v2-review"
                  onClick={() => onReview(card)}
                  aria-label={`Review ${pendingWorkSubjectLabelV2(card.subjectKind).toLowerCase()}`}
                >
                  Review →
                </button>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
      {partial ? (
        <p data-testid="pending-work-v2-partial" style={noticeStyle}>
          Some runtime work couldn't be loaded.
        </p>
      ) : null}
      {stale ? (
        <p data-testid="pending-work-v2-stale" style={noticeStyle}>
          Runtime work may be out of date.
        </p>
      ) : null}
      {hasMore ? (
        <div style={loadMoreRowStyle}>
          <button
            type="button"
            className="ui-button ui-button--sm"
            data-testid="pending-work-v2-load-more"
            onClick={onLoadMore}
            disabled={loading}
          >
            {loading ? "Loading…" : "Load more"}
          </button>
        </div>
      ) : null}
    </section>
  );
}

const sectionStyle: CSSProperties = {
  display: "grid",
  gap: "var(--space-sm, 8px)",
  padding: "var(--space-sm, 8px)",
  borderBottom: "1px solid var(--color-border, #22252e)",
};

const headingStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: "var(--space-sm, 8px)",
};

const listStyle: CSSProperties = {
  display: "grid",
  gap: "var(--space-sm, 8px)",
  listStyle: "none",
  margin: 0,
  padding: 0,
};

const cardStyle: CSSProperties = {
  display: "grid",
  gap: "var(--space-2xs, 4px)",
  padding: "var(--space-sm, 8px) var(--space-md, 12px)",
  border: "1px solid var(--color-border, #22252e)",
  borderRadius: "var(--radius-md, 8px)",
  background: "var(--color-bg-surface, #1b1d24)",
};

const cardHeadStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: "var(--space-sm, 8px)",
};

const copyStyle: CSSProperties = {
  margin: 0,
  color: "var(--color-text-muted, #9aa0aa)",
};

const noticeStyle: CSSProperties = {
  margin: 0,
  color: "var(--color-text-muted, #9aa0aa)",
};

const footerStyle: CSSProperties = {
  display: "flex",
  justifyContent: "flex-end",
};

const loadMoreRowStyle: CSSProperties = {
  display: "flex",
  justifyContent: "flex-end",
};
