export {
  SheetRenderer,
  registerSheetAdapter,
  renderSheetDiff,
  sheetAdapter,
  type SheetCellChange,
  type SheetCellValue,
  type SheetDiff,
  type SheetRegion,
  // PR-3.10 (FR-3.21) — per-row inline approval states.
  type SheetRowApproval,
  type SheetRowApprovalState,
} from "./SheetRenderer";

export { SheetDiff as SheetDiffView, type SheetDiffProps } from "./SheetDiff";
