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

import type { CSSProperties, ReactElement } from "react";

import type { LedgerStagedWrite } from "./ledgerProjection";

export interface TcApproveBarProps {
  readonly stage: LedgerStagedWrite;
  /** Approve the pinned latest rev (host POSTs `/decisions {approve, rev}`). */
  readonly onApprove: (stageId: string, rev: number) => void;
  /** Reject the staged write (host POSTs `/decisions {reject, rev}`). */
  readonly onReject: (stageId: string, rev: number) => void;
  /** Restore a rejected staged write (host POSTs `/decisions {restore}`). */
  readonly onRestore: (stageId: string) => void;
  /** Disables the actions while a decision is in flight. */
  readonly busy?: boolean;
}

/** The exact, load-bearing WYSIWYG copy. `{N}` is the pinned latest rev. */
export function approveBarMicrocopy(rev: number): string {
  return `Exactly this draft — rev ${rev} — is what sends.`;
}

const rootStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-sm)",
  flexWrap: "wrap",
  padding: "var(--space-sm) var(--space-md)",
  borderTop: "1px solid var(--color-border-subtle)",
};

const copyStyle: CSSProperties = { flex: "1 1 auto" };
const actionsStyle: CSSProperties = {
  display: "flex",
  gap: "var(--space-sm)",
  alignItems: "center",
};

export function TcApproveBar({
  stage,
  onApprove,
  onReject,
  onRestore,
  busy = false,
}: TcApproveBarProps): ReactElement {
  const pinnedRev = stage.latestRev;
  const isRejected = stage.status === "rejected";
  const isDecided = stage.status === "approved" || stage.status === "applied";

  return (
    <div className="ui-card" style={rootStyle} data-testid="tc-approve-bar">
      <span
        className="ui-body"
        style={copyStyle}
        data-testid="tc-approve-bar-copy"
      >
        {isRejected
          ? "This draft was rejected — nothing sends."
          : approveBarMicrocopy(pinnedRev)}
      </span>

      <span className="ui-mono-caps" data-testid="tc-approve-bar-ledger-id">
        {stage.ledgerId}
      </span>

      <div style={actionsStyle}>
        {isRejected ? (
          <button
            type="button"
            className="ui-button ui-button--primary"
            disabled={busy}
            onClick={() => onRestore(stage.stageId)}
            data-testid="tc-approve-bar-restore"
          >
            Restore
          </button>
        ) : (
          <>
            <button
              type="button"
              className="ui-button ui-button--primary"
              disabled={busy || isDecided}
              onClick={() => onApprove(stage.stageId, pinnedRev)}
              data-testid="tc-approve-bar-approve"
            >
              {isDecided ? "Approved" : `Approve rev ${pinnedRev}`}
            </button>
            <button
              type="button"
              className="ui-button"
              disabled={busy || isDecided}
              onClick={() => onReject(stage.stageId, pinnedRev)}
              data-testid="tc-approve-bar-reject"
            >
              Reject
            </button>
          </>
        )}
      </div>
    </div>
  );
}
