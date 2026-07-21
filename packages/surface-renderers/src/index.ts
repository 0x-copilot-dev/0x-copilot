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

// === Wave 1 (PRD-03) archetype renderers ===
import { registerArchetypeAdapters } from "./archetypes";
// === end Wave 1 ===

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

// === Wave 1 (PRD-03) archetype renderers ===
export {
  ARCHETYPE_ADAPTERS,
  registerArchetypeAdapters,
  RecordRenderer,
  RecordDiffRenderer,
  recordAdapter,
  TableRenderer,
  TableDiffRenderer,
  tableAdapter,
  MessageRenderer,
  MessageDiffRenderer,
  messageAdapter,
  DocRenderer,
  DocDiffRenderer,
  docAdapter,
  BoardRenderer,
  BoardDiffRenderer,
  boardAdapter,
} from "./archetypes";
export {
  formatValue,
  isSafeHttpUrl,
  resolvePath,
  MAX_DISPLAY_CHARS,
} from "./_shared/path";
export {
  changesFromDiff,
  dataFromState,
  specFromState,
  type SurfaceArchetype,
  type SurfaceColumn,
  type SurfaceDiff,
  type SurfaceEnvelope,
  type SurfaceField,
  type SurfaceFieldChange,
  type SurfaceFieldFormat,
  type SurfaceLink,
  type SurfaceSpec,
  type SurfaceState,
} from "./_shared/specTypes";
// === end Wave 1 ===

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
  // === Wave 1 (PRD-03) archetype renderers ===
  registerArchetypeAdapters();
  // === end Wave 1 ===
}
