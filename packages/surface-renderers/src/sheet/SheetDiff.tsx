import type { CSSProperties, ReactElement } from "react";

import { TcInlineDiff } from "@0x-copilot/chat-surface";

import type {
  SheetCellChange,
  SheetCellValue,
  SheetDiff as SheetDiffData,
  SheetRegion,
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
  lime: "#c2ff5a",
  limeBgSoft: "rgba(194, 255, 90, 0.10)",
  changed: "#c2ff5a",
  changedBg: "rgba(194, 255, 90, 0.16)",
  removed: "#ef5a5a",
} as const;

function changeKey(row: number, column: number): string {
  return `${row}:${column}`;
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
}

export function SheetDiff(props: SheetDiffProps): ReactElement {
  const { diff } = props;
  const region = diff.region;
  const columnWindow = resolveColumnWindow(region);
  const visibleHeaders = region.headers.slice(
    columnWindow.startColumn,
    columnWindow.endColumn,
  );
  const changeIndex = buildChangeIndex(diff.changes);

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
              </tr>
            </thead>
            <tbody>
              {region.rows.map((row, rowIdx) => {
                const visibleCells = row.slice(
                  columnWindow.startColumn,
                  columnWindow.endColumn,
                );
                return (
                  <tr
                    key={`row-${rowIdx}`}
                    data-testid={`sheet-diff-row-${rowIdx}`}
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
