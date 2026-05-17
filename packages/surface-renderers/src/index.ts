import { registerEmailAdapter } from "./email";

// === Phase 4-D salesforce ===
import { registerSalesforceAdapter } from "./salesforce";
// === end Phase 4-D ===

// === Phase 4-E tier1-sheets ===
import { registerSheetAdapter } from "./sheet";
// === end Phase 4-E ===

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
} from "./sheet";
// === end Phase 4-E ===

export function registerAll(): void {
  registerEmailAdapter();
  // === Phase 4-D salesforce ===
  registerSalesforceAdapter();
  // === end Phase 4-D ===
  // === Phase 4-E tier1-sheets ===
  registerSheetAdapter();
  // === end Phase 4-E ===
}
