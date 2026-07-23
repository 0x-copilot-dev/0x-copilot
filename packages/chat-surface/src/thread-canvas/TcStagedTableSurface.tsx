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

import type { CSSProperties, ReactElement } from "react";

import { Badge } from "@0x-copilot/design-system";

import { TcBulkApplyBar } from "./TcBulkApplyBar";
import type { LedgerStagedRow, LedgerStagedWrite } from "./ledgerProjection";

export interface TcStagedTableSurfaceProps {
  readonly stage: LedgerStagedWrite;
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

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-sm)",
};

const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-sm)",
  flexWrap: "wrap",
  padding: "var(--space-md) var(--space-md) 0",
};

const spacerStyle: CSSProperties = { flex: "1 1 auto" };

const rowStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
  padding: "var(--space-sm) var(--space-md)",
  borderTop: "1px solid var(--color-border-subtle)",
};

const rowHeadStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-sm)",
  flexWrap: "wrap",
};

const heldReasonStyle: CSSProperties = {
  color: "var(--color-text-warning, var(--color-text-secondary))",
};

const diffStyle: CSSProperties = { margin: 0 };
const oldStyle: CSSProperties = {
  textDecoration: "line-through",
  opacity: 0.6,
};

const footerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-sm)",
  padding: "6px var(--space-md)",
  borderTop: "1px solid var(--color-border-subtle)",
};

const rowActionsStyle: CSSProperties = {
  display: "flex",
  gap: "var(--space-sm)",
  alignItems: "center",
};

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

  return (
    <div style={rowStyle} data-testid="tc-table-row" data-row-key={row.rowKey}>
      <div style={rowHeadStyle}>
        <span className="ui-section-label" data-testid="tc-table-row-title">
          {row.title}
        </span>
        {held ? (
          <Badge tone="warning" data-testid="tc-table-row-held">
            held
          </Badge>
        ) : (
          <Badge tone="neutral" data-testid="tc-table-row-will-apply">
            will apply
          </Badge>
        )}
        {row.applyOutcome !== null ? (
          <Badge
            tone={row.applyOutcome === "applied" ? "success" : "warning"}
            data-testid="tc-table-row-outcome"
          >
            {row.applyOutcome === "applied" ? "updated" : "failed"}
          </Badge>
        ) : null}
        <span style={spacerStyle} aria-hidden="true" />
        {editable ? (
          <div style={rowActionsStyle}>
            {held ? (
              <button
                type="button"
                className="ui-button"
                onClick={() =>
                  onRowDecision(stage.stageId, "approve", row.rowKey)
                }
                data-testid="tc-table-row-approve"
              >
                Approve
              </button>
            ) : (
              <button
                type="button"
                className="ui-button"
                onClick={() => onRowDecision(stage.stageId, "hold", row.rowKey)}
                data-testid="tc-table-row-hold"
              >
                Hold
              </button>
            )}
          </div>
        ) : null}
      </div>

      {/* Agent pre-hold reason — STILL visible after a user override (FR-C7). */}
      {row.agentHoldReason !== null && row.agentHoldReason !== "" ? (
        <span
          className="ui-caption"
          style={heldReasonStyle}
          data-testid="tc-table-row-reason"
        >
          {`${row.agentHoldReason} — agent pre-held`}
        </span>
      ) : null}

      {row.changes.map((change, i) => (
        <p
          key={`${row.rowKey}-${change.field}-${i}`}
          className="ui-caption"
          style={diffStyle}
          data-testid="tc-table-row-change"
        >
          <span>{`${change.field}: `}</span>
          <span style={oldStyle}>{renderValue(change.old)}</span>
          <span>{` → ${renderValue(change.new)}`}</span>
        </p>
      ))}
    </div>
  );
}

export function TcStagedTableSurface({
  stage,
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
  const terminal = isApplied || isPartial;

  return (
    <div className="ui-card" style={rootStyle} data-testid="tc-staged-table">
      <div style={headerStyle}>
        <span
          className="ui-section-label"
          data-testid="tc-staged-table-connector"
        >
          {stage.target.connector !== ""
            ? stage.target.connector
            : "Bulk change"}
        </span>
        <span style={spacerStyle} aria-hidden="true" />
        <span className="ui-caption" data-testid="tc-staged-table-counts">
          {terminal ? resultLine(applied, held) : countsHeader(willApply, held)}
        </span>
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

      <div style={footerStyle}>
        <Badge
          tone={isApplied ? "success" : "warning"}
          data-testid="tc-staged-table-access"
        >
          {isApplied ? "write · applied" : "write · held"}
        </Badge>
        <span className="ui-mono-caps" data-testid="tc-staged-table-ledger-id">
          {stage.ledgerId}
        </span>
      </div>

      {/* The apply bar drops once the row-set reaches a terminal state (nothing
          left to apply); a failed apply folds back to `staged`, returning it. */}
      {!terminal ? (
        <TcBulkApplyBar stage={stage} onApply={onApply} busy={busy} />
      ) : null}
    </div>
  );
}
