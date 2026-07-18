import type { CSSProperties, ReactElement } from "react";

import { TcInlineDiff } from "@0x-copilot/chat-surface";

import type {
  SheetCellChange,
  SheetCellValue,
  SheetDiff as SheetDiffData,
  SheetRegion,
  SheetRowApproval,
} from "./SheetRenderer";
import { resolveColumnWindow } from "./_columns";

const PALETTE = {
  pageBg: "#0F1218",
  surface: "#131722",
  surfaceMute: "#181c25",
  border: "#22252E",
  borderStrong: "#2a2d31",
  textHi: "#E4E5E9",
  textMid: "#c8ccd1",
  textLo: "#7E8492",
  lime: "var(--color-accent)",
  limeBgSoft: "color-mix(in srgb, var(--color-accent) 10%, transparent)",
  changed: "var(--color-accent)",
  changedBg: "color-mix(in srgb, var(--color-accent) 16%, transparent)",
  removed: "#ef5a5a",
} as const;

function changeKey(row: number, column: number): string {
  return `${row}:${column}`;
}

// PR-3.10 (FR-3.21) — index the per-row approvals by row for O(1) lookup.
function buildApprovalIndex(
  approvals: readonly SheetRowApproval[] | undefined,
): ReadonlyMap<number, SheetRowApproval> {
  const map = new Map<number, SheetRowApproval>();
  if (approvals === undefined) {
    return map;
  }
  for (const approval of approvals) {
    map.set(approval.row, approval);
  }
  return map;
}

function buildChangeIndex(
  changes: readonly SheetCellChange[],
): ReadonlyMap<string, SheetCellChange> {
  const map = new Map<string, SheetCellChange>();
  for (const change of changes) {
    map.set(changeKey(change.row, change.column), change);
  }
  return map;
}

function formatCellValue(cell: SheetCellValue): string {
  if (cell.value === null) {
    return "";
  }
  return String(cell.value);
}

function cellAriaLabel(
  header: string,
  change: SheetCellChange | undefined,
  fallback: SheetCellValue,
): string {
  if (change) {
    return `${header}: ${formatCellValue(change.before)} → ${formatCellValue(change.after)}`;
  }
  return `${header}: ${formatCellValue(fallback)}`;
}

export interface SheetDiffProps {
  readonly diff: SheetDiffData;
  /**
   * PR-3.10 (FR-3.21) — resolve a pending row's approval. Fired by the row's
   * `Approve & sign` button; the host owns the POST (D28 pure-render rule).
   */
  readonly onApproveRow?: (approval: SheetRowApproval) => void;
  /** PR-3.10 (FR-3.21) — reject a pending row's approval (host owns the POST). */
  readonly onRejectRow?: (approval: SheetRowApproval) => void;
  /** PR-3.10 — primary approve label ("Approve & sign" default; "Approve"). */
  readonly approveLabel?: string;
}

export function SheetDiff(props: SheetDiffProps): ReactElement {
  const {
    diff,
    onApproveRow,
    onRejectRow,
    approveLabel = "Approve & sign",
  } = props;
  const region = diff.region;
  const columnWindow = resolveColumnWindow(region);
  const visibleHeaders = region.headers.slice(
    columnWindow.startColumn,
    columnWindow.endColumn,
  );
  const changeIndex = buildChangeIndex(diff.changes);
  // PR-3.10 (FR-3.21) — per-row approval states drive an appended approval
  // column (highlight + Reject/Approve on pending; settled status otherwise).
  const approvalIndex = buildApprovalIndex(diff.rowApprovals);
  const hasApprovals = approvalIndex.size > 0;

  return (
    <div
      data-testid="sheet-diff"
      data-diff-id={diff.diffId}
      data-sheet-id={region.sheetId}
      data-region-id={region.regionId}
      data-virtualized={columnWindow.virtualized ? "true" : "false"}
      data-visible-columns={visibleHeaders.length}
      data-total-columns={columnWindow.totalColumns}
      data-changes={diff.changes.length}
      style={wrapperStyle}
    >
      <div style={pillRowStyle}>
        <TcInlineDiff
          state="streaming"
          progressPercent={diff.streamProgress}
          provenance={diff.provenance}
          title={diff.title}
          description={diff.description}
        />
      </div>
      {visibleHeaders.length === 0 ? (
        <div
          style={emptyStyle}
          data-testid="sheet-diff-empty"
          aria-label="Empty sheet region"
        >
          No columns to display
        </div>
      ) : (
        <div style={tableContainerStyle}>
          <table role="table" style={tableStyle}>
            <thead>
              <tr>
                {visibleHeaders.map((header, idx) => (
                  <th
                    key={`${columnWindow.startColumn + idx}-${header}`}
                    scope="col"
                    style={headerCellStyle}
                    data-testid={`sheet-diff-header-${columnWindow.startColumn + idx}`}
                  >
                    {header}
                  </th>
                ))}
                {hasApprovals ? (
                  <th
                    scope="col"
                    style={headerCellStyle}
                    data-testid="sheet-diff-approval-header"
                    aria-label="Approval"
                  >
                    Approval
                  </th>
                ) : null}
              </tr>
            </thead>
            <tbody>
              {region.rows.map((row, rowIdx) => {
                const visibleCells = row.slice(
                  columnWindow.startColumn,
                  columnWindow.endColumn,
                );
                const approval = approvalIndex.get(rowIdx);
                const isPending = approval?.state === "pending";
                return (
                  <tr
                    key={`row-${rowIdx}`}
                    data-testid={`sheet-diff-row-${rowIdx}`}
                    data-approval-state={approval?.state ?? "none"}
                    style={isPending ? pendingRowStyle : undefined}
                  >
                    {visibleCells.map((cell, cellIdx) => {
                      const absoluteCol = columnWindow.startColumn + cellIdx;
                      const change = changeIndex.get(
                        changeKey(rowIdx, absoluteCol),
                      );
                      const headerLabel = visibleHeaders[cellIdx];
                      const ariaLabel = cellAriaLabel(
                        headerLabel,
                        change,
                        cell,
                      );
                      if (change) {
                        return (
                          <td
                            key={`cell-${rowIdx}-${absoluteCol}`}
                            data-testid={`sheet-diff-cell-${rowIdx}-${absoluteCol}`}
                            data-changed="true"
                            aria-label={ariaLabel}
                            style={changedCellStyle}
                          >
                            <del
                              style={removedValueStyle}
                              data-testid={`sheet-diff-before-${rowIdx}-${absoluteCol}`}
                            >
                              {formatCellValue(change.before)}
                            </del>
                            <div
                              style={changedValueStyle}
                              data-testid={`sheet-diff-after-${rowIdx}-${absoluteCol}`}
                            >
                              {formatCellValue(change.after)}
                            </div>
                          </td>
                        );
                      }
                      return (
                        <td
                          key={`cell-${rowIdx}-${absoluteCol}`}
                          data-testid={`sheet-diff-cell-${rowIdx}-${absoluteCol}`}
                          data-changed="false"
                          aria-label={ariaLabel}
                          style={dataCellStyle}
                        >
                          <div style={valueStyle}>{formatCellValue(cell)}</div>
                        </td>
                      );
                    })}
                    {hasApprovals ? (
                      <ApprovalCell
                        rowIdx={rowIdx}
                        approval={approval}
                        approveLabel={approveLabel}
                        onApproveRow={onApproveRow}
                        onRejectRow={onRejectRow}
                      />
                    ) : null}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// PR-3.10 (FR-3.21) — the appended per-row approval cell. Pending rows show
// `Reject` / `Approve & sign` (host-owned callbacks); resolved rows show their
// settled status (`✓ Signed` jade / `Rejected` ember / `Queued` muted).
interface ApprovalCellProps {
  readonly rowIdx: number;
  readonly approval: SheetRowApproval | undefined;
  readonly approveLabel: string;
  readonly onApproveRow?: (approval: SheetRowApproval) => void;
  readonly onRejectRow?: (approval: SheetRowApproval) => void;
}

function ApprovalCell(props: ApprovalCellProps): ReactElement {
  const { rowIdx, approval, approveLabel, onApproveRow, onRejectRow } = props;
  if (approval === undefined) {
    return (
      <td
        data-testid={`sheet-diff-approval-${rowIdx}`}
        data-approval-state="none"
        style={approvalCellStyle}
      />
    );
  }
  if (approval.state === "pending") {
    return (
      <td
        data-testid={`sheet-diff-approval-${rowIdx}`}
        data-approval-state="pending"
        style={approvalCellStyle}
      >
        <div
          role="group"
          aria-label={`Row ${rowIdx + 1} approval`}
          style={approvalActionsStyle}
        >
          <button
            type="button"
            data-testid={`sheet-diff-row-reject-${rowIdx}`}
            onClick={() => onRejectRow?.(approval)}
            style={rejectButtonStyle}
          >
            Reject
          </button>
          <button
            type="button"
            data-testid={`sheet-diff-row-approve-${rowIdx}`}
            onClick={() => onApproveRow?.(approval)}
            style={approveButtonStyle}
          >
            {approveLabel}
          </button>
        </div>
      </td>
    );
  }
  return (
    <td
      data-testid={`sheet-diff-approval-${rowIdx}`}
      data-approval-state={approval.state}
      style={approvalCellStyle}
    >
      <span
        data-testid={`sheet-diff-row-status-${rowIdx}`}
        style={statusStyle(approval.state)}
      >
        {statusLabel(approval.state)}
      </span>
    </td>
  );
}

function statusLabel(state: SheetRowApproval["state"]): string {
  switch (state) {
    case "signed":
      return "✓ Signed";
    case "rejected":
      return "Rejected";
    case "queued":
      return "Queued";
    default:
      return "";
  }
}

function statusStyle(state: SheetRowApproval["state"]): CSSProperties {
  const color =
    state === "signed"
      ? "var(--color-success, #57c785)"
      : state === "rejected"
        ? "var(--color-danger, #f0764f)"
        : "var(--color-text-muted, #7E8492)";
  return {
    fontSize: 12,
    fontWeight: 600,
    whiteSpace: "nowrap",
    color,
  };
}

const wrapperStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
  fontFamily:
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
};

const pillRowStyle: CSSProperties = {
  display: "flex",
  justifyContent: "flex-start",
};

const tableContainerStyle: CSSProperties = {
  background: PALETTE.pageBg,
  border: `1px solid ${PALETTE.border}`,
  borderRadius: 10,
  padding: 12,
  color: PALETTE.textHi,
  overflowX: "auto",
  maxWidth: "100%",
};

const tableStyle: CSSProperties = {
  borderCollapse: "separate",
  borderSpacing: 0,
  fontSize: 12,
  width: "max-content",
  minWidth: "100%",
};

const headerCellStyle: CSSProperties = {
  position: "sticky",
  top: 0,
  background: PALETTE.surfaceMute,
  color: PALETTE.textLo,
  textAlign: "left",
  padding: "8px 12px",
  borderBottom: `1px solid ${PALETTE.borderStrong}`,
  borderRight: `1px solid ${PALETTE.border}`,
  fontWeight: 600,
  letterSpacing: 0.4,
  textTransform: "uppercase",
  fontSize: 11,
  whiteSpace: "nowrap",
};

const dataCellStyle: CSSProperties = {
  padding: "8px 12px",
  borderBottom: `1px solid ${PALETTE.border}`,
  borderRight: `1px solid ${PALETTE.border}`,
  verticalAlign: "top",
  background: PALETTE.surface,
  color: PALETTE.textHi,
  whiteSpace: "nowrap",
};

const changedCellStyle: CSSProperties = {
  padding: "8px 12px",
  borderBottom: `1px solid ${PALETTE.border}`,
  borderRight: `1px solid ${PALETTE.border}`,
  verticalAlign: "top",
  background: PALETTE.changedBg,
  color: PALETTE.textHi,
  whiteSpace: "nowrap",
  boxShadow: `inset 0 0 0 1px ${PALETTE.changed}`,
};

const valueStyle: CSSProperties = {
  fontSize: 13,
  lineHeight: 1.4,
};

const changedValueStyle: CSSProperties = {
  fontSize: 13,
  lineHeight: 1.4,
  color: PALETTE.textHi,
  fontWeight: 600,
};

const removedValueStyle: CSSProperties = {
  fontSize: 12,
  lineHeight: 1.3,
  color: PALETTE.removed,
  display: "block",
  textDecorationColor: PALETTE.removed,
};

const emptyStyle: CSSProperties = {
  background: PALETTE.pageBg,
  border: `1px dashed ${PALETTE.border}`,
  borderRadius: 10,
  padding: 24,
  color: PALETTE.textLo,
  fontSize: 13,
  textAlign: "center",
};

// PR-3.10 (FR-3.21) — a pending row is highlighted with an accent-soft fill and
// an inset accent bar down its leading edge (design-system tokens only).
const pendingRowStyle: CSSProperties = {
  background: "var(--color-accent-soft, rgba(95,178,236,.12))",
  boxShadow: "inset 3px 0 0 0 var(--color-accent)",
};

const approvalCellStyle: CSSProperties = {
  padding: "8px 12px",
  borderBottom: `1px solid ${PALETTE.border}`,
  verticalAlign: "middle",
  background: PALETTE.surface,
  whiteSpace: "nowrap",
};

const approvalActionsStyle: CSSProperties = {
  display: "flex",
  gap: 6,
  justifyContent: "flex-end",
};

const approveButtonStyle: CSSProperties = {
  background: "var(--color-accent)",
  color: "var(--color-accent-contrast, #0F1218)",
  border: "none",
  borderRadius: 6,
  padding: "4px 10px",
  fontSize: 12,
  fontWeight: 600,
  cursor: "pointer",
  whiteSpace: "nowrap",
};

const rejectButtonStyle: CSSProperties = {
  background: "transparent",
  color: PALETTE.textHi,
  border: `1px solid ${PALETTE.borderStrong}`,
  borderRadius: 6,
  padding: "4px 10px",
  fontSize: 12,
  fontWeight: 600,
  cursor: "pointer",
  whiteSpace: "nowrap",
};
