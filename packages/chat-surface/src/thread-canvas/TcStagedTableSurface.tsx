// Staged bulk-table surface (Generative Surfaces v2, PRD-D3). 🎨
//
// The table-archetype surface a row-set write stages onto: per-row title + old→new
// field diffs, a per-row Approve/Hold toggle, the agent pre-hold warning chip
// (rendered `{reason} — agent pre-held`, STILL visible after a user override —
// FR-C7), a live counts header ("6 will apply · 2 held"), and the applied / partial
// result line ("7 updated · 1 held, untouched" — FR-C9). Renders directly from a
// `LedgerStagedWrite` (folded from the ledger); every action is a host callback
// threaded through the composed `TcBulkApplyBar`.
//
// Pure presentational: no port/clock/browser reads. Kit-only styling (design-system
// recipes + tokens); no raw font-size / letter-spacing.

import type { ReactElement } from "react";

import { Badge } from "@0x-copilot/design-system";

import { TcBulkApplyBar } from "./TcBulkApplyBar";
import type { LedgerStagedRow, LedgerStagedWrite } from "./ledgerProjection";

export interface TcStagedTableSurfaceProps {
  readonly stage: LedgerStagedWrite;
  /** Surface-authored display title; falls back without connector guessing. */
  readonly title?: string;
  /** Optional surface-authored count/status summary for the table header. */
  readonly summary?: string;
  /** Optional precondition/recovery note shown beside the apply action. */
  readonly reviewNotice?: string;
  /** Toggle a row's stance (host POSTs `/decisions {approve|hold, row_keys}`). */
  readonly onRowDecision: (
    stageId: string,
    decision: "approve" | "hold",
    rowKey: string,
  ) => void;
  /** Apply exactly the will-apply set (host POSTs `/apply {rev, row_keys}`). */
  readonly onApply: (
    stageId: string,
    rev: number,
    rowKeys: readonly string[],
  ) => void;
  readonly busy?: boolean;
}

/** Live counts header, e.g. "6 will apply · 2 held". */
export function countsHeader(willApply: number, held: number): string {
  return `${willApply} will apply · ${held} held`;
}

/** Applied / partial result line (FR-C9), e.g. "7 updated · 1 held, untouched". */
export function resultLine(applied: number, held: number): string {
  const heldPart = held > 0 ? ` · ${held} held, untouched` : "";
  return `${applied} updated${heldPart}`;
}

function renderValue(value: unknown): string {
  if (value === null || value === undefined) return "∅";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function StagedRowView({
  stage,
  row,
  onRowDecision,
  busy,
}: {
  stage: LedgerStagedWrite;
  row: LedgerStagedRow;
  onRowDecision: TcStagedTableSurfaceProps["onRowDecision"];
  busy: boolean;
}): ReactElement {
  const held = row.stance === "held";
  const editable =
    stage.status === "staged" && !busy && row.applyOutcome === null;
  const outcomeLabel =
    row.applyOutcome === "applied"
      ? "updated"
      : row.applyOutcome === "failed"
        ? "failed"
        : held
          ? "held"
          : "approved";

  return (
    <div
      className={`tc-review-table__row${
        held ? " tc-review-table__row--held" : ""
      }${row.applyOutcome === "failed" ? " tc-review-table__row--failed" : ""}`}
      data-testid="tc-table-row"
      data-row-key={row.rowKey}
      role="row"
    >
      <div className="tc-review-table__decisions" role="cell">
        <button
          type="button"
          className="tc-review-table__decision tc-review-table__decision--approve"
          title="Approve this change"
          aria-label={`Approve ${row.title}`}
          aria-pressed={!held}
          disabled={!editable}
          onClick={() => onRowDecision(stage.stageId, "approve", row.rowKey)}
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
          disabled={!editable}
          onClick={() => onRowDecision(stage.stageId, "hold", row.rowKey)}
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

      <div className="tc-review-table__title" role="cell">
        <strong data-testid="tc-table-row-title">{row.title}</strong>
      </div>

      <div
        className="tc-review-table__old"
        data-testid="tc-table-row-old"
        role="cell"
      >
        {row.changes.map((change, index) => (
          <span key={`${row.rowKey}-old-${change.field}-${index}`}>
            {renderValue(change.old)}
          </span>
        ))}
      </div>

      <div
        className="tc-review-table__change"
        data-testid="tc-table-row-change"
        role="cell"
      >
        {row.changes.map((change, index) => (
          <span key={`${row.rowKey}-new-${change.field}-${index}`}>
            <small>{change.field}</small>
            <span
              className="tc-review-table__change-value"
              data-testid="tc-table-row-change-value"
            >
              {renderValue(change.new)}
            </span>
          </span>
        ))}
      </div>

      <div className="tc-review-table__note" role="cell">
        {/* Agent pre-hold reason remains visible after a user override (FR-C7). */}
        {row.agentHoldReason !== null && row.agentHoldReason !== "" ? (
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
        ) : row.applyOutcome === "failed" ? (
          <span>Write failed — retry available</span>
        ) : (
          <span>{held ? "Held — untouched" : "Ready to apply"}</span>
        )}
      </div>

      <div className="tc-review-table__status" role="cell">
        {row.applyOutcome !== null ? (
          <span
            className={`tc-review-table__outcome tc-review-table__outcome--${row.applyOutcome}`}
            data-testid="tc-table-row-outcome"
          >
            {row.applyOutcome === "applied" ? (
              <svg
                viewBox="0 0 24 24"
                width="11"
                height="11"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.4"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M5 12l5 5L20 7" />
              </svg>
            ) : (
              <svg
                viewBox="0 0 24 24"
                width="11"
                height="11"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M18 6 6 18M6 6l12 12" />
              </svg>
            )}
            {outcomeLabel}
          </span>
        ) : (
          <Badge
            tone={held ? "warning" : "neutral"}
            data-testid={held ? "tc-table-row-held" : "tc-table-row-will-apply"}
          >
            {outcomeLabel}
          </Badge>
        )}
      </div>
    </div>
  );
}

export function TcStagedTableSurface({
  stage,
  title,
  summary,
  reviewNotice,
  onRowDecision,
  onApply,
  busy = false,
}: TcStagedTableSurfaceProps): ReactElement {
  const counts = stage.rowCounts;
  const willApply = counts?.willApply ?? 0;
  const held = counts?.held ?? 0;
  const applied = counts?.applied ?? 0;
  const rows = stage.rows ?? [];
  const isApplied = stage.status === "applied";
  const isPartial = stage.status === "partially_applied";

  return (
    <div
      className="tc-review-surface tc-review-surface--table"
      data-testid="tc-staged-table"
      data-state={stage.status}
    >
      <header className="tc-review-table__header">
        <span
          className="tc-review-table__title-heading"
          data-testid="tc-staged-table-connector"
        >
          {title ??
            `${rows.length} ${
              stage.target.connector !== "" ? stage.target.connector : "bulk"
            } changes`}
        </span>
        <Badge
          tone={isApplied ? "success" : "warning"}
          data-testid="tc-staged-table-access"
        >
          {isApplied
            ? "applied"
            : isPartial
              ? "partial · retry available"
              : "staged, not applied"}
        </Badge>
        <span
          className="tc-review-table__counts"
          data-testid="tc-staged-table-counts"
        >
          {summary ??
            (isApplied || isPartial
              ? resultLine(applied, held)
              : countsHeader(willApply, held))}
        </span>
      </header>

      <div className="tc-review-table" role="table">
        <div className="tc-review-table__columns" role="row">
          <span role="columnheader">Decide</span>
          <span role="columnheader">Item</span>
          <span role="columnheader">Previous</span>
          <span role="columnheader">Change</span>
          <span role="columnheader">Review note</span>
          <span role="columnheader">Status</span>
        </div>

        {rows.map((row) => (
          <StagedRowView
            key={row.rowKey}
            stage={stage}
            row={row}
            onRowDecision={onRowDecision}
            busy={busy}
          />
        ))}
      </div>

      {/* A partial result exposes a real recovery command for exactly the
          failed subset. Applied rows remain immutable and are never resent. */}
      {!isApplied ? (
        <TcBulkApplyBar
          stage={stage}
          onApply={onApply}
          message={reviewNotice}
          busy={busy}
        />
      ) : null}

      <footer className="tc-review-provenance">
        <span className="tc-review-provenance__kind">Table</span>
        <span
          className="tc-review-provenance__detail"
          data-testid="tc-staged-table-ledger-id"
        >
          {`${stage.target.connector}.${stage.target.op} · per-row approval · ${stage.ledgerId}`}
        </span>
      </footer>
    </div>
  );
}
