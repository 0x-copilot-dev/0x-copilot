// Bulk apply bar for a staged row-set (Generative Surfaces v2, PRD-D3). 🎨
//
// The scope-naming apply affordance: "Apply {N} changes →" where N is the CURRENT
// will-apply count. Applying sends `{rev, row_keys}` = exactly the displayed set —
// the server re-checks equality (WYSIWYG). Pure presentational: renders from a
// `LedgerStagedWrite` (folded) and fires host callbacks; never reads a port/clock/
// browser primitive. Kit-only styling; no raw font-size / letter-spacing.

import type { CSSProperties, ReactElement } from "react";

import type { LedgerStagedWrite } from "./ledgerProjection";

export interface TcBulkApplyBarProps {
  readonly stage: LedgerStagedWrite;
  /** Apply exactly the current will-apply set (host POSTs `/apply {rev, row_keys}`). */
  readonly onApply: (
    stageId: string,
    rev: number,
    rowKeys: readonly string[],
  ) => void;
  readonly busy?: boolean;
}

/** The exact, contract-grade pledge microcopy (FR-C6) — do not reword. */
export const bulkApplyPledge =
  "Writes apply only to rows you approve. Held rows stay untouched.";

/** The exact apply-action label. `{N}` is the current will-apply count. */
export function bulkApplyLabel(n: number): string {
  return `Apply ${n} changes →`;
}

const rootStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-sm)",
  flexWrap: "wrap",
  padding: "var(--space-sm) var(--space-md)",
  borderTop: "1px solid var(--color-border-subtle)",
};

const pledgeStyle: CSSProperties = { flex: "1 1 auto" };

export function TcBulkApplyBar({
  stage,
  onApply,
  busy = false,
}: TcBulkApplyBarProps): ReactElement {
  const counts = stage.rowCounts;
  const willApply = counts?.willApply ?? 0;
  const willApplyKeys = (stage.rows ?? [])
    .filter((r) => r.stance === "will_apply")
    .map((r) => r.rowKey);
  const frozen =
    stage.status === "apply_pending" ||
    stage.status === "applied" ||
    stage.status === "partially_applied";

  return (
    <div className="ui-card" style={rootStyle} data-testid="tc-bulk-apply-bar">
      <span
        className="ui-caption"
        style={pledgeStyle}
        data-testid="tc-bulk-pledge"
      >
        {bulkApplyPledge}
      </span>
      <span className="ui-mono-caps" data-testid="tc-bulk-ledger-id">
        {stage.ledgerId}
      </span>
      <button
        type="button"
        className="ui-button ui-button--primary"
        disabled={busy || frozen || willApply === 0}
        onClick={() => onApply(stage.stageId, stage.latestRev, willApplyKeys)}
        data-testid="tc-bulk-apply"
      >
        {frozen && stage.status === "apply_pending"
          ? "Applying…"
          : bulkApplyLabel(willApply)}
      </button>
    </div>
  );
}
