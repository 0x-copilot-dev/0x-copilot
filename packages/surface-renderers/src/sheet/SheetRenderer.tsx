import type { CSSProperties, ReactElement } from "react";

import {
  registerAdapter,
  type SaaSRendererAdapter,
} from "@enterprise-search/chat-surface";

import { SheetDiff as SheetDiffView } from "./SheetDiff";

export interface SheetCellValue {
  readonly value: string | number | null;
  readonly formula?: string;
  readonly format?: "text" | "number" | "currency" | "percent" | "date";
}

export interface SheetRegion {
  readonly sheetId: string;
  readonly regionId: string;
  readonly headers: readonly string[];
  readonly rows: readonly (readonly SheetCellValue[])[];
  readonly rowAnchors?: readonly string[];
  readonly viewport?: {
    readonly startColumn: number;
    readonly endColumn: number;
  };
}

export interface SheetCellChange {
  readonly row: number;
  readonly column: number;
  readonly before: SheetCellValue;
  readonly after: SheetCellValue;
}

export interface SheetDiff {
  readonly diffId: string;
  readonly provenance: string;
  readonly title: string;
  readonly description?: string;
  readonly region: SheetRegion;
  readonly changes: readonly SheetCellChange[];
}

const VIRTUALIZATION_THRESHOLD = 50;
const DEFAULT_VIEWPORT_WIDTH = 50;

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
  changedBg: "rgba(194, 255, 90, 0.12)",
  removed: "#ef5a5a",
} as const;

interface ColumnWindow {
  readonly startColumn: number;
  readonly endColumn: number;
  readonly virtualized: boolean;
  readonly totalColumns: number;
}

function resolveColumnWindow(region: SheetRegion): ColumnWindow {
  const totalColumns = region.headers.length;
  if (totalColumns < VIRTUALIZATION_THRESHOLD) {
    return {
      startColumn: 0,
      endColumn: totalColumns,
      virtualized: false,
      totalColumns,
    };
  }
  const requested = region.viewport;
  if (!requested) {
    return {
      startColumn: 0,
      endColumn: Math.min(DEFAULT_VIEWPORT_WIDTH, totalColumns),
      virtualized: true,
      totalColumns,
    };
  }
  const start = Math.max(0, Math.min(requested.startColumn, totalColumns));
  const end = Math.max(start, Math.min(requested.endColumn, totalColumns));
  return {
    startColumn: start,
    endColumn: end,
    virtualized: true,
    totalColumns,
  };
}

function formatCellValue(cell: SheetCellValue): string {
  if (cell.value === null) {
    return "";
  }
  return String(cell.value);
}

function formulaLabel(
  cell: SheetCellValue,
  rowAnchor: string | undefined,
): string {
  const formula = cell.formula ?? "";
  if (rowAnchor && rowAnchor.length > 0) {
    return `${rowAnchor} ${formula}`;
  }
  return formula;
}

function renderEmptyRegion(region: SheetRegion): ReactElement {
  return (
    <div
      data-testid="sheet-renderer"
      data-sheet-id={region.sheetId}
      data-region-id={region.regionId}
      data-empty="true"
      aria-label="Empty sheet region"
      style={emptyStyle}
    >
      No columns to display
    </div>
  );
}

export function SheetRenderer(region: SheetRegion): ReactElement {
  if (region.headers.length === 0) {
    return renderEmptyRegion(region);
  }
  const columnWindow = resolveColumnWindow(region);
  const visibleHeaders = region.headers.slice(
    columnWindow.startColumn,
    columnWindow.endColumn,
  );
  return (
    <div
      data-testid="sheet-renderer"
      data-sheet-id={region.sheetId}
      data-region-id={region.regionId}
      data-virtualized={columnWindow.virtualized ? "true" : "false"}
      data-visible-columns={visibleHeaders.length}
      data-total-columns={columnWindow.totalColumns}
      style={containerStyle}
    >
      <table role="table" style={tableStyle}>
        <thead>
          <tr>
            {visibleHeaders.map((header, idx) => (
              <th
                key={`${columnWindow.startColumn + idx}-${header}`}
                scope="col"
                style={headerCellStyle}
                data-testid={`sheet-header-${columnWindow.startColumn + idx}`}
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
            const rowAnchor = region.rowAnchors?.[rowIdx];
            return (
              <tr
                key={`row-${rowIdx}`}
                data-testid={`sheet-row-${rowIdx}`}
                data-row-anchor={rowAnchor ?? ""}
              >
                {visibleCells.map((cell, cellIdx) => {
                  const absoluteCol = columnWindow.startColumn + cellIdx;
                  return (
                    <td
                      key={`cell-${rowIdx}-${absoluteCol}`}
                      data-testid={`sheet-cell-${rowIdx}-${absoluteCol}`}
                      data-has-formula={cell.formula ? "true" : "false"}
                      style={dataCellStyle}
                    >
                      <div style={valueStyle}>{formatCellValue(cell)}</div>
                      {cell.formula ? (
                        <div
                          style={formulaBarStyle}
                          aria-readonly="true"
                          data-testid={`sheet-formula-${rowIdx}-${absoluteCol}`}
                        >
                          {formulaLabel(cell, rowAnchor)}
                        </div>
                      ) : null}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function renderSheetDiff(diff: SheetDiff): ReactElement {
  return <SheetDiffView diff={diff} />;
}

export const sheetAdapter: SaaSRendererAdapter<SheetRegion, SheetDiff> = {
  scheme: "sheet-row",
  matches: (uri) => uri.startsWith("sheet-row://"),
  renderCurrent: (region) => SheetRenderer(region),
  renderDiff: (diff) => renderSheetDiff(diff),
  metadata: {
    origin: "first-party",
    schemaVersion: 1,
  },
};

export function registerSheetAdapter(): void {
  registerAdapter(sheetAdapter as SaaSRendererAdapter);
}

const containerStyle: CSSProperties = {
  background: PALETTE.pageBg,
  border: `1px solid ${PALETTE.border}`,
  borderRadius: 10,
  padding: 12,
  color: PALETTE.textHi,
  fontFamily:
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
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

const valueStyle: CSSProperties = {
  fontSize: 13,
  lineHeight: 1.4,
};

const formulaBarStyle: CSSProperties = {
  marginTop: 4,
  padding: "2px 6px",
  background: PALETTE.limeBgSoft,
  color: PALETTE.lime,
  borderRadius: 4,
  fontFamily:
    "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace",
  fontSize: 11,
  letterSpacing: 0.2,
};

const emptyStyle: CSSProperties = {
  background: PALETTE.pageBg,
  border: `1px dashed ${PALETTE.border}`,
  borderRadius: 10,
  padding: 24,
  color: PALETTE.textLo,
  fontFamily:
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
  fontSize: 13,
  textAlign: "center",
};
