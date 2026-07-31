// The approval consent card — the design's three `.conf-card` shapes.
//
// One frame, three bodies, chosen by `presentation.layout`:
//
//   params  — a key/value frame. The default, and what every approval rendered
//             before shapes existed.
//   rows    — a batch of line items, each with its own decision. Rejecting one
//             payee must not mean killing the run.
//   preview — the literal content about to leave the workspace. A params table
//             can tell you a post is going to #launch; only the draft itself can
//             tell you whether it should.
//
// Every string from `presentation` is model-authored (the backend projects it
// from the real tool-call arguments) and is rendered as PLAIN TEXT — no markdown,
// no HTML. The reassurance line and the vendor pill are NOT part of that block:
// they are server-derived claims and are passed separately, so a model cannot
// write a reassuring sentence under an irreversible action.

import type { ReactElement, ReactNode } from "react";
import type { ActivityParam } from "./types";
import type { ApprovalPresentation, ApprovalRow } from "./presentation";

export interface ConsentCardProps {
  /** Verb-first title ("Sign payout batch"). */
  readonly title: string;
  /** Server-derived shape + narrative. Null renders the params frame. */
  readonly presentation: ApprovalPresentation | null;
  /** Key/value rows for the `params` shape. */
  readonly params?: readonly ActivityParam[];
  /** Persistent rule line. Server-derived — never sourced from `presentation`. */
  readonly reassurance: string;
  /** Leading glyph; defaults to the shield. */
  readonly icon?: ReactNode;
  readonly onApprove?: () => void;
  readonly onReject?: () => void;
  /** Per-row decision for a `rows` card. Omit to render rows read-only. */
  readonly onRowDecision?: (rowId: string, approved: boolean) => void;
  /** Show the ⌘↵ / ⌘⌫ chords on the primary actions. */
  readonly showChords?: boolean;
  readonly testId?: string;
  /**
   * Host-supplied ids for the two decision controls. The host's tests (and
   * its keyboard-chord wiring) address these buttons by name, and those names
   * predate this component — so the host keeps naming them rather than every
   * call site migrating to whatever this file happens to call them.
   */
  readonly approveTestId?: string;
  readonly rejectTestId?: string;
}

const DEFAULT_APPROVE = "Approve";
const DEFAULT_REJECT = "Reject";

export function ConsentCard({
  title,
  presentation,
  params = [],
  reassurance,
  icon,
  onApprove,
  onReject,
  onRowDecision,
  showChords = false,
  testId,
  approveTestId = "apc-approve",
  rejectTestId = "apc-reject",
}: ConsentCardProps): ReactElement {
  const layout = presentation?.layout ?? "params";
  return (
    <div
      className="apc"
      data-layout={layout}
      data-testid={testId}
      role="group"
      aria-label={`Approval: ${title}`}
      // The standing rule is no longer a visible row (see below); keep it
      // available to assistive tech so the claim is not simply deleted.
      aria-description={reassurance}
    >
      <div className="apc__head">
        <span className="apc__icon" aria-hidden="true">
          {icon ?? <ShieldGlyph />}
        </span>
        <span className="apc__title">{title}</span>
        {presentation?.provenance != null ? (
          <span className="apc__provenance">{presentation.provenance}</span>
        ) : null}
      </div>

      {layout === "rows" && presentation !== null ? (
        <RowsBody rows={presentation.rows} onRowDecision={onRowDecision} />
      ) : null}

      {layout === "preview" && presentation?.preview != null ? (
        <p className="apc__preview">
          {presentation.preview.text}
          {presentation.preview.meta !== null ? (
            <span className="apc__preview-meta">
              {presentation.preview.meta}
            </span>
          ) : null}
        </p>
      ) : null}

      {layout === "params" && params.length > 0 ? (
        <div className="apc__params">
          {params.map((param) => (
            <dl className="apc__param" key={`${param.label}-${param.value}`}>
              <dt>{param.label}</dt>
              <dd>{param.value}</dd>
            </dl>
          ))}
        </div>
      ) : null}

      <div className="apc__actions">
        <button
          type="button"
          className="apc-btn apc-btn--reject"
          data-testid={rejectTestId}
          onClick={onReject}
        >
          {presentation?.rejectLabel ?? DEFAULT_REJECT}
          {showChords ? (
            <span className="apc-btn__chord" aria-hidden="true">
              ⌘⌫
            </span>
          ) : null}
        </button>
        <button
          type="button"
          className="apc-btn apc-btn--primary"
          data-testid={approveTestId}
          onClick={onApprove}
        >
          {presentation?.approveLabel ?? DEFAULT_APPROVE}
          {showChords ? (
            <span className="apc-btn__chord" aria-hidden="true">
              ⌘↵
            </span>
          ) : null}
        </button>
      </div>

      {/* The standing "you're always asked…" line is NOT rendered. It repeated
          the same sentence on every card, so it stopped being read and only
          cost height in the chat column. Removed as an ELEMENT rather than
          hidden by CSS: a `display:none` override sat above the original
          `.apc__foot { display:flex }` in the same stylesheet, lost the
          cascade to it, and shipped twice looking fixed. `reassurance` is kept
          as the card's accessible description so the claim survives for
          screen readers without occupying a row. */}
    </div>
  );
}

function RowsBody({
  rows,
  onRowDecision,
}: {
  readonly rows: readonly ApprovalRow[];
  readonly onRowDecision?: (rowId: string, approved: boolean) => void;
}): ReactElement {
  return (
    <>
      {rows.map((row, index) => (
        <div className="apc__row" key={row.rowId ?? `${row.label}-${index}`}>
          <span className="apc__row-name">
            {row.initials !== null ? (
              <span className="apc__avatar" aria-hidden="true">
                {row.initials}
              </span>
            ) : null}
            <span className="apc__row-label">{row.label}</span>
            {row.note !== null ? (
              <span className="apc__row-note">{row.note}</span>
            ) : null}
          </span>
          <span className="apc__row-value">{row.value}</span>
          {rowTail(row, onRowDecision)}
        </div>
      ))}
    </>
  );
}

function rowTail(
  row: ApprovalRow,
  onRowDecision?: (rowId: string, approved: boolean) => void,
): ReactNode {
  if (row.status !== "pending") {
    return (
      <span className="apc__row-status" data-status={row.status}>
        {row.status === "approved" ? <CheckGlyph /> : null}
        {statusLabel(row.status)}
      </span>
    );
  }
  // A row can only carry a decision when the host wired one AND the row has a
  // stable id to send it under. Otherwise it renders as part of the batch and
  // the card-level Approve covers it.
  if (onRowDecision === undefined || row.rowId === null || !row.decidable) {
    return null;
  }
  const rowId = row.rowId;
  return (
    <span className="apc__row-actions">
      <button
        type="button"
        className="apc-btn apc-btn--reject"
        data-testid={`apc-row-reject-${rowId}`}
        onClick={() => onRowDecision(rowId, false)}
      >
        Reject
      </button>
      <button
        type="button"
        className="apc-btn apc-btn--primary"
        data-testid={`apc-row-approve-${rowId}`}
        onClick={() => onRowDecision(rowId, true)}
      >
        Approve
      </button>
    </span>
  );
}

function statusLabel(status: ApprovalRow["status"]): string {
  switch (status) {
    case "approved":
      return "Signed";
    case "rejected":
      return "Rejected";
    default:
      return "Queued";
  }
}

function ShieldGlyph(): ReactElement {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M8 1.5 2.5 3.75v3.5c0 3.25 2.25 6.25 5.5 7.25 3.25-1 5.5-4 5.5-7.25v-3.5L8 1.5Z" />
    </svg>
  );
}

function CheckGlyph(): ReactElement {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="m3 8.5 3.2 3.2L13 5" />
    </svg>
  );
}
