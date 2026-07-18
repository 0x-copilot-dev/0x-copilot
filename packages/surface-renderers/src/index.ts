import { registerEmailAdapter } from "./email";

// === Phase 4-D salesforce ===
import { registerSalesforceAdapter } from "./salesforce";
// === end Phase 4-D ===

// === Phase 4-E tier1-sheets ===
import { registerSheetAdapter } from "./sheet";
// === end Phase 4-E ===

// === Phase 4-F tier1-slides ===
import { registerSlideAdapter } from "./slide";
// === end Phase 4-F ===

export {
  emailAdapter,
  registerEmailAdapter,
  type EmailDiff,
  type EmailDiffPending,
  type EmailState,
} from "./email";

// === Phase 4-D salesforce ===
export {
  OpportunityRenderer,
  OpportunityDiffRenderer,
  OpportunityFieldRow,
  opportunityAdapter,
  type SalesforceOpportunity,
  type SalesforceOpportunityCustomField,
  type SalesforceOpportunityDiff,
  type SalesforceOpportunityFieldChange,
  type OpportunityFieldRowProps,
} from "./salesforce";
// === end Phase 4-D ===

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
  // PR-3.10 (FR-3.21) — per-row inline approval states.
  type SheetRowApproval,
  type SheetRowApprovalState,
} from "./sheet";
// === end Phase 4-E ===

// === Phase 4-F tier1-slides ===
export {
  SlideRenderer,
  SlideDiff,
  slideAdapter,
  registerSlideAdapter,
  type Slide,
  type SlideBullet,
  type SlideRendererProps,
  type SlideDiffPayload,
  type SlideDiffProps,
} from "./slide";
// === end Phase 4-F ===

export function registerAll(): void {
  registerEmailAdapter();
  // === Phase 4-D salesforce ===
  registerSalesforceAdapter();
  // === end Phase 4-D ===
  // === Phase 4-E tier1-sheets ===
  registerSheetAdapter();
  // === end Phase 4-E ===
  // === Phase 4-F tier1-slides ===
  registerSlideAdapter();
  // === end Phase 4-F ===
}
