import type { SheetRegion } from "./SheetRenderer";

export const VIRTUALIZATION_THRESHOLD = 50;
export const DEFAULT_VIEWPORT_WIDTH = 50;

export interface ColumnWindow {
  readonly startColumn: number;
  readonly endColumn: number;
  readonly virtualized: boolean;
  readonly totalColumns: number;
}

export function resolveColumnWindow(region: SheetRegion): ColumnWindow {
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
