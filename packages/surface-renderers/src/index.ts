import { registerEmailSurface } from "./email";

// === Phase 4-E tier1-sheets ===
import { registerSheetAdapter } from "./sheet";
// === end Phase 4-E ===

export {
  EmailRenderer,
  EmailDiffOverlay,
  type EmailDiffOverlayProps,
} from "./email";

// === Phase 4-E tier1-sheets ===
export {
  SheetRenderer,
  SheetDiffView,
  registerSheetAdapter,
  renderSheetDiff,
  sheetAdapter,
  type SheetCellChange,
  type SheetCellValue,
  type SheetDiff,
  type SheetDiffProps,
  type SheetRegion,
} from "./sheet";
// === end Phase 4-E ===

export function registerAll(): void {
  registerEmailSurface();
  // === Phase 4-E tier1-sheets ===
  registerSheetAdapter();
  // === end Phase 4-E ===
}
