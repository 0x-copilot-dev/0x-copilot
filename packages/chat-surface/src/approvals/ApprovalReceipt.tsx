// Collapsed scrollback record for a settled approval (PR-1.6, moved from
// apps/frontend/.../activity/ApprovalReceipt.tsx).
//
// Once the user (or the chain) has decided, the bulky ApprovalCard
// becomes a single line — same vocabulary as a HarnessRow but tagged
// for the approval domain so audit views can pick them out:
//
//     ✓ Approved · List Linear issues · 10:42
//     ✕ Denied   · Post to #launch-aurora · 10:43
//     ↗ Forwarded to @marcus · 10:41
//     ⏸ Cancelled · 10:44
//
// Click expands ``details`` (full args + result) for auditing. The
// component is intentionally aria-static — keyboard activation just
// toggles the <details> element below. Presentational only: the undo
// action is a host-driven ``onUndo`` callback (D28 pure-render rule);
// the host owns the POST.

import { classNames } from "@0x-copilot/design-system";
import type { ReactElement, ReactNode } from "react";
import { ActivityDetails } from "./ActivityDetails";
import { useUndoCountdown } from "./useUndoCountdown";

export type ApprovalReceiptKind =
  | "approved"
  | "rejected"
  | "forwarded"
  | "cancelled"
  | "chain-approved"
  | "chain-rejected";

export interface ApprovalReceiptProps {
  kind: ApprovalReceiptKind;
  /** "List Linear issues" / "Post to #launch-aurora". */
  title: string;
  /** Optional trailing text — "by @marcus", "to @sarah", "10:42". */
  meta?: ReactNode;
  /** Tool details collapsible — debugger surface. */
  details?: ReactNode;
  detailsLabel?: string;
  className?: string;
  /** PR 4.4.6.4 — when set and in the future, render an undo button
   *  with a live countdown. ``null`` / past → no button. */
  undoUntil?: Date | null;
  /** PR 4.4.6.4 — when set, the user has already requested undo;
   *  render a passive "Undo requested" chip instead of the button. */
  undoRequestedAt?: Date | null;
  /** PR 4.4.6.4 — invoked when the user clicks the active Undo button.
   *  Caller is responsible for the POST + state update. */
  onUndo?: () => void;
  /** PR 4.4.6.4 — disables the button while a request is in flight. */
  undoPending?: boolean;
}

const GLYPH: Record<ApprovalReceiptKind, string> = {
  approved: "✓",
  rejected: "✕",
  forwarded: "↗",
  cancelled: "⏸",
  "chain-approved": "✓",
  "chain-rejected": "✕",
};

const LABEL: Record<ApprovalReceiptKind, string> = {
  approved: "Approved",
  rejected: "Denied",
  forwarded: "Forwarded",
  cancelled: "Cancelled",
  "chain-approved": "Approved",
  "chain-rejected": "Denied",
};

export function ApprovalReceipt({
  kind,
  title,
  meta,
  details,
  detailsLabel = "Tool details",
  className,
  undoUntil,
  undoRequestedAt,
  onUndo,
  undoPending,
}: ApprovalReceiptProps): ReactElement {
  const { secondsRemaining, expired } = useUndoCountdown(undoUntil ?? null);
  // PR 4.4.6.4 — "Undo requested" wins when set; otherwise the active
  // button renders only inside the window. Past expiry → nothing.
  const showRequested =
    undoRequestedAt !== undefined && undoRequestedAt !== null;
  const showButton =
    !showRequested && undoUntil !== undefined && undoUntil !== null && !expired;
  return (
    <div
      className={classNames("atlas-approval-receipt", className)}
      data-kind={kind}
      role="note"
    >
      <span className="atlas-approval-receipt__glyph" aria-hidden="true">
        {GLYPH[kind]}
      </span>
      <span className="atlas-approval-receipt__label">{LABEL[kind]}</span>
      <span className="atlas-approval-receipt__sep" aria-hidden="true">
        ·
      </span>
      <span className="atlas-approval-receipt__title">{title}</span>
      {meta ? (
        <>
          <span className="atlas-approval-receipt__sep" aria-hidden="true">
            ·
          </span>
          <span className="atlas-approval-receipt__meta">{meta}</span>
        </>
      ) : null}
      {showButton ? (
        <>
          <span className="atlas-approval-receipt__sep" aria-hidden="true">
            ·
          </span>
          <button
            type="button"
            className="atlas-approval-receipt__undo"
            disabled={undoPending}
            onClick={onUndo}
          >
            {undoPending ? "Undoing…" : `Undo (${secondsRemaining}s)`}
          </button>
        </>
      ) : null}
      {showRequested ? (
        <>
          <span className="atlas-approval-receipt__sep" aria-hidden="true">
            ·
          </span>
          <span
            className="atlas-approval-receipt__undo-requested"
            title="Audit-logged. Vendor revert is a follow-up."
          >
            Undo requested
          </span>
        </>
      ) : null}
      {details ? (
        <ActivityDetails label={detailsLabel}>{details}</ActivityDetails>
      ) : null}
    </div>
  );
}
