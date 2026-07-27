// Rev-pinned approve bar for a staged write (Generative Surfaces v2, PRD-D1). 🎨
//
// The what-you-approve-is-what-executes bar pinned to a staged draft. Pure
// presentational: renders directly from a `LedgerStagedWrite` (folded from the
// `write.staged` / `revision.added` / `decision.recorded` ledger events) and
// fires host callbacks — it never reads a port, clock, or browser primitive and
// never posts a `/decisions` itself. Kit-only styling (design-system recipes +
// tokens); no raw font-size / letter-spacing.
//
// The microcopy is EXACT and load-bearing (FR-C3, the WYSIWYG pin): the approve
// affordance always names the rev it will send. Approving a stale rev is the
// server's 409 to enforce, not the bar's — the bar always pins `latestRev`.

import type { ReactElement } from "react";

import type { LedgerStagedWrite } from "./ledgerProjection";

export interface TcApproveBarProps {
  readonly stage: LedgerStagedWrite;
  /** Approve the pinned latest rev (host POSTs `/decisions {approve, rev}`). */
  readonly onApprove: (stageId: string, rev: number) => void;
  /** Reject the staged write (host POSTs `/decisions {reject, rev}`). */
  readonly onReject: (stageId: string, rev: number) => void;
  /** Restore a rejected staged write (host POSTs `/decisions {restore}`). */
  readonly onRestore: (stageId: string) => void;
  /** Opens the revision editor while preserving this approval boundary. */
  readonly onEdit?: () => void;
  /** Disables the actions while a decision is in flight. */
  readonly busy?: boolean;
  /** Keeps the rev-pinned approval context visible while an edit is active. */
  readonly suspended?: boolean;
}

/** The exact, load-bearing WYSIWYG copy. `{N}` is the pinned latest rev. */
export function approveBarMicrocopy(rev: number): string {
  return `Exactly this draft — rev ${rev} — is what sends.`;
}

export function TcApproveBar({
  stage,
  onApprove,
  onReject,
  onRestore,
  onEdit,
  busy = false,
  suspended = false,
}: TcApproveBarProps): ReactElement {
  const pinnedRev = stage.latestRev;
  const isRejected = stage.status === "rejected";
  const isDecided = stage.status === "approved" || stage.status === "applied";
  const disabled = busy || suspended;

  return (
    <div
      className="tc-review-action-bar tc-review-action-bar--approval"
      data-testid="tc-approve-bar"
      data-suspended={suspended ? "true" : undefined}
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
        <path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" />
      </svg>
      <span
        className="tc-review-action-bar__copy"
        data-testid="tc-approve-bar-copy"
      >
        {isRejected ? (
          "This draft was rejected — nothing sends."
        ) : (
          <>
            Exactly this draft — <strong>{`rev ${pinnedRev}`}</strong> — is what
            sends.
          </>
        )}
      </span>

      <span
        className="tc-review-action-bar__ledger"
        data-testid="tc-approve-bar-ledger-id"
      >
        {stage.ledgerId}
      </span>

      <div className="tc-review-action-bar__actions">
        {isRejected ? (
          <button
            type="button"
            className="ui-button ui-button--sm"
            disabled={disabled}
            onClick={() => onRestore(stage.stageId)}
            data-testid="tc-approve-bar-restore"
          >
            Restore
          </button>
        ) : (
          <>
            <button
              type="button"
              className="ui-button ui-button--sm ui-button--danger"
              disabled={busy || isDecided}
              onClick={() => onReject(stage.stageId, pinnedRev)}
              data-testid="tc-approve-bar-reject"
            >
              Reject
            </button>
            {onEdit !== undefined ? (
              <button
                type="button"
                className="ui-button ui-button--sm"
                disabled={disabled || isDecided}
                onClick={onEdit}
                data-testid="tc-staged-draft-edit"
              >
                <svg
                  viewBox="0 0 24 24"
                  width="12"
                  height="12"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.7"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z" />
                </svg>
                Edit draft
              </button>
            ) : null}
            <button
              type="button"
              className="ui-button ui-button--sm ui-button--primary"
              disabled={disabled || isDecided}
              onClick={() => onApprove(stage.stageId, pinnedRev)}
              data-testid="tc-approve-bar-approve"
            >
              {isDecided ? "Approved" : "Approve & send →"}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
