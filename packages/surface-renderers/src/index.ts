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
import { registerArtifactAdapters } from "./artifacts";
// === end Wave 1 ===

export {
  emailAdapter,
  emailStateFrom,
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
  IMPLEMENTED_ARCHETYPES,
  registerArchetypeAdapters,
  // Editable surface Phase 2 — the connector half. A `table://` surface renders
  // as editable cells when, and only when, the host attached a write-back grant
  // (`hostConnectorEditor` reads it off the render state). Save STAGES; the
  // decision is taken at the write gate that already exists.
  EditableConnectorTable,
  EDITABLE_ROW_CAP,
  hostConnectorEditor,
  asConnectorRow,
  buildConnectorRowEdits,
  cellKey,
  editableCellText,
  isEditableCell,
  rowKeyFor,
  rowsOf,
  rowTitleFor,
  type ConnectorEditsResult,
  type ConnectorFieldChange,
  type ConnectorRow,
  type ConnectorRowEdit,
  type ConnectorSurfaceEditorActions,
  type EditableConnectorTableProps,
  type PendingCellEdits,
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
  ARTIFACT_ADAPTERS,
  ArtifactRenderer,
  CodeArtifactRenderer,
  DatasetArtifactRenderer,
  DocumentArtifactRenderer,
  // Editable surface Phase 1. `ArtifactEditorActions` is the ONLY thing a host
  // wires to make a document editable — a `{disabled, saveRevision}` object it
  // builds around its own transport. Editability is never on the wire and never
  // on a `SurfaceSpec`; a surface the host did not open renders read-only.
  EditableDocument,
  FileArtifactRenderer,
  RawArtifactFallback,
  hostEditorActions,
  parseCsv,
  registerArtifactAdapters,
  type ArtifactEditorActions,
  type ArtifactRenderState,
  type ArtifactRevisionSaveOutcome,
  type EditableDocumentProps,
} from "./artifacts";
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
  registerArtifactAdapters();
  // === end Wave 1 ===
}
