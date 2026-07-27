// Bulk apply bar for a staged row-set (Generative Surfaces v2, PRD-D3). 🎨
//
// The scope-naming apply affordance: "Apply {N} changes →" where N is the CURRENT
// will-apply count. Applying sends `{rev, row_keys}` = exactly the displayed set —
// the server re-checks equality (WYSIWYG). Pure presentational: renders from a
// `LedgerStagedWrite` (folded) and fires host callbacks; never reads a port/clock/
// browser primitive. Kit-only styling; no raw font-size / letter-spacing.

import type { ReactElement } from "react";

import type { LedgerStagedWrite } from "./ledgerProjection";

export interface TcBulkApplyBarProps {
  readonly stage: LedgerStagedWrite;
  /** Apply exactly the current will-apply set (host POSTs `/apply {rev, row_keys}`). */
  readonly onApply: (
    stageId: string,
    rev: number,
    rowKeys: readonly string[],
  ) => void;
  /** Surface-authored safety/recovery note; defaults to the generic contract. */
  readonly message?: string;
  readonly busy?: boolean;
}

/** The exact, contract-grade pledge microcopy (FR-C6) — do not reword. */
export const bulkApplyPledge =
  "Writes apply only to rows you approve. Held rows stay untouched.";

/** The exact apply-action label. `{N}` is the current will-apply count. */
export function bulkApplyLabel(n: number): string {
  return `Apply ${n} changes →`;
}

/** Recovery label for the exact failed subset after a partial apply. */
export function bulkRetryLabel(n: number): string {
  return `Retry ${n} failed →`;
}

export const bulkRetryPledge =
  "Some writes failed. Applied rows are safe — nothing lost.";

export function bulkRetryMessage(n: number, connector?: string): string {
  const atConnector =
    connector !== undefined && connector.trim() !== ""
      ? ` at ${connector.trim()}`
      : "";
  return `${n} writes failed${atConnector} · successes kept — nothing lost, the successes stuck.`;
}

export function TcBulkApplyBar({
  stage,
  onApply,
  message,
  busy = false,
}: TcBulkApplyBarProps): ReactElement {
  const recovery = stage.status === "partially_applied";
  const willApplyKeys = (stage.rows ?? [])
    .filter(
      (row) =>
        row.stance === "will_apply" &&
        (recovery ? row.applyOutcome === "failed" : row.applyOutcome === null),
    )
    .map((r) => r.rowKey);
  const actionCount = willApplyKeys.length;
  const frozen = stage.status === "apply_pending" || stage.status === "applied";

  return (
    <div
      className={`tc-review-action-bar ${
        recovery
          ? "tc-review-action-bar--recovery"
          : "tc-review-action-bar--approval"
      }`}
      data-testid="tc-bulk-apply-bar"
      data-mode={recovery ? "recovery" : "apply"}
    >
      <svg
        className="tc-review-action-bar__icon"
        viewBox="0 0 24 24"
        width="15"
        height="15"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        {recovery ? (
          <>
            <path d="M20 11a8.1 8.1 0 1 0-2.4 5.8" />
            <path d="M20 4v7h-7" />
          </>
        ) : (
          <path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" />
        )}
      </svg>
      <span className="tc-review-action-bar__copy" data-testid="tc-bulk-pledge">
        {message !== undefined ? (
          message
        ) : recovery ? (
          <>
            {`${actionCount} writes failed${
              stage.target.connector.trim() !== ""
                ? ` at ${stage.target.connector.trim()}`
                : ""
            } · successes kept — `}
            <strong>nothing lost</strong>, the successes stuck.
          </>
        ) : (
          bulkApplyPledge
        )}
      </span>
      <span
        className="tc-review-action-bar__ledger"
        data-testid="tc-bulk-ledger-id"
      >
        {stage.ledgerId}
      </span>
      <button
        type="button"
        className="ui-button ui-button--sm ui-button--primary"
        disabled={busy || frozen || actionCount === 0}
        onClick={() => onApply(stage.stageId, stage.latestRev, willApplyKeys)}
        data-testid={recovery ? "tc-bulk-retry" : "tc-bulk-apply"}
      >
        {frozen && stage.status === "apply_pending"
          ? "Applying…"
          : recovery
            ? bulkRetryLabel(actionCount)
            : bulkApplyLabel(actionCount)}
      </button>
    </div>
  );
}
