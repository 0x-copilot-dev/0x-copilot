// Connector-independent staged row-set review surface.
//
// The component consumes only `RowsetReviewModel`: durable ledger state and
// command eligibility are normalized before render. The shared ReviewSurface
// recipes own bounded layout, overflow, diffs, statuses, actions, and
// provenance. No connector identity selects a component or layout.

import type { ReactElement } from "react";

import {
  DiffValue,
  ReviewFooter,
  ReviewHeader,
  ReviewSurface,
  ReviewTable,
  StatusMark,
} from "./ReviewSurface";
import { TcBulkApplyBar } from "./TcBulkApplyBar";
import { rowsetCountsSummary, rowsetResultSummary } from "./rowsetReviewModel";
import type {
  RowsetActionContext,
  RowsetDecisionContext,
  RowsetReviewModel,
  RowsetReviewRow,
} from "./rowsetReviewModel";

// "Sending", not "Change": the column lists every argument the row will send,
// including the ones the user never touched. A column called Change would be a
// promise the contents deliberately break.
const REVIEW_COLUMNS = [
  "Decide",
  "Item",
  "Currently",
  "Sending",
  "Review note",
  "Status",
] as const;

/** Copy for a row whose outbound arguments were not disclosed. The server
 *  refuses to stage such a row; this is the second arm, so a run replayed from
 *  an older ledger cannot be approved through this surface either. */
const UNACCOUNTED_NOTE =
  "Cannot be reviewed — this row's outbound fields were not disclosed.";

export interface TcStagedTableSurfaceProps {
  readonly model: RowsetReviewModel;
  /** Toggle one row's stance through the stage-decision command. */
  readonly onRowDecision: (decision: RowsetDecisionContext) => void;
  /** Submit the model's immutable exact action scope unchanged. */
  readonly onApply: (action: RowsetActionContext) => void;
}

/** Backward-compatible summary helpers, now backed by the semantic model. */
export function countsHeader(willApply: number, held: number): string {
  return rowsetCountsSummary({ approved: willApply, held });
}

export function resultLine(applied: number, held: number): string {
  return rowsetResultSummary({ applied, held, failed: 0 });
}

function DecisionControls({
  model,
  row,
  onRowDecision,
}: {
  readonly model: RowsetReviewModel;
  readonly row: RowsetReviewRow;
  readonly onRowDecision: TcStagedTableSurfaceProps["onRowDecision"];
}): ReactElement {
  const held = row.decision === "held";
  return (
    <div className="tc-review-table__decisions" role="cell">
      <button
        type="button"
        className="tc-review-table__decision tc-review-table__decision--approve"
        title="Approve this change"
        aria-label={`Approve ${row.title}`}
        aria-pressed={!held}
        disabled={!row.canDecide}
        onClick={() =>
          onRowDecision({
            stageId: model.stageId,
            revision: model.revision,
            proposalDigest: model.proposalDigest,
            targetDigest: model.targetDigest,
            rowKey: row.rowKey,
            decision: "approve",
            basisSequence: model.lastSequence,
          })
        }
        data-testid="tc-table-row-approve"
      >
        <svg
          viewBox="0 0 24 24"
          width="12"
          height="12"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.4"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M5 12l5 5L20 7" />
        </svg>
      </button>
      <button
        type="button"
        className="tc-review-table__decision tc-review-table__decision--hold"
        title="Hold — keep as is"
        aria-label={`Hold ${row.title}`}
        aria-pressed={held}
        disabled={!row.canDecide}
        onClick={() =>
          onRowDecision({
            stageId: model.stageId,
            revision: model.revision,
            proposalDigest: model.proposalDigest,
            targetDigest: model.targetDigest,
            rowKey: row.rowKey,
            decision: "hold",
            basisSequence: model.lastSequence,
          })
        }
        data-testid="tc-table-row-hold"
      >
        <svg
          viewBox="0 0 24 24"
          width="12"
          height="12"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.2"
          strokeLinecap="round"
          aria-hidden="true"
        >
          <path d="M6 6l12 12M18 6L6 18" />
        </svg>
      </button>
    </div>
  );
}

function ReviewNote({ row }: { readonly row: RowsetReviewRow }): ReactElement {
  return (
    <div className="tc-review-table__note" role="cell">
      {row.unaccounted ? (
        <div data-testid="tc-table-row-unaccounted">
          <svg
            viewBox="0 0 24 24"
            width="10"
            height="10"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M12 3l10 17H2z" />
            <path d="M12 9v5M12 17.5v.5" />
          </svg>
          {UNACCOUNTED_NOTE}
        </div>
      ) : row.agentHoldReason !== null && row.agentHoldReason !== "" ? (
        <div data-testid="tc-table-row-reason">
          <svg
            viewBox="0 0 24 24"
            width="10"
            height="10"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M12 3l10 17H2z" />
            <path d="M12 9v5M12 17.5v.5" />
          </svg>
          {`${row.agentHoldReason} — agent pre-held`}
        </div>
      ) : row.outcome === "failed" ? (
        <span>Write failed — retry available</span>
      ) : (
        <span>
          {row.decision === "held" ? "Held — untouched" : "Ready to apply"}
        </span>
      )}
    </div>
  );
}

function ReviewRow({
  model,
  row,
  onRowDecision,
}: {
  readonly model: RowsetReviewModel;
  readonly row: RowsetReviewRow;
  readonly onRowDecision: TcStagedTableSurfaceProps["onRowDecision"];
}): ReactElement {
  return (
    <div
      className={`tc-review-table__row${
        row.decision === "held" ? " tc-review-table__row--held" : ""
      }${row.outcome === "failed" ? " tc-review-table__row--failed" : ""}${
        row.unaccounted ? " tc-review-table__row--unaccounted" : ""
      }`}
      data-testid="tc-table-row"
      data-row-key={row.rowKey}
      data-unaccounted={row.unaccounted ? "true" : undefined}
      role="row"
    >
      <DecisionControls model={model} row={row} onRowDecision={onRowDecision} />
      <div className="tc-review-table__title" role="cell">
        <strong data-testid="tc-table-row-title" title={row.title}>
          {row.title}
        </strong>
      </div>
      <DiffValue diffs={row.diffs} side="before" />
      <DiffValue diffs={row.diffs} side="after" />
      <ReviewNote row={row} />
      <StatusMark row={row} />
    </div>
  );
}

export function TcStagedTableSurface({
  model,
  onRowDecision,
  onApply,
}: TcStagedTableSurfaceProps): ReactElement {
  return (
    <ReviewSurface kind="rowset" testId="tc-staged-table" state={model.status}>
      <ReviewHeader
        title={model.title}
        status={model.statusMark}
        summary={model.summary}
      />
      <ReviewTable
        label={`${model.title} row review`}
        columns={REVIEW_COLUMNS}
        rowCount={model.rows.length}
      >
        {model.rows.map((row) => (
          <ReviewRow
            key={row.rowKey}
            model={model}
            row={row}
            onRowDecision={onRowDecision}
          />
        ))}
      </ReviewTable>
      {model.action !== null ? (
        <TcBulkApplyBar action={model.action} onApply={onApply} />
      ) : null}
      <ReviewFooter provenance={model.provenance} />
    </ReviewSurface>
  );
}
