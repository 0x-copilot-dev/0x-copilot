// Library destination — public surface + ItemRef resolver registration.
//
// Per cross-audit §1.1 + §3.3 (binding 2026-05-17), each destination
// registers its kind on package import. Library owns the resolvers for
// kinds `"library_file"` / `"library_page"` / `"library_dataset"` so
// every other destination's `<ItemLink kind="library_*" id=…>` resolves
// without forcing a circular dependency.
//
// Wire-type re-exports are forwarded from `_library-stub.ts`; the
// orchestrator rewires the stub to `@0x-copilot/api-types` at
// merge time.

import {
  LibraryDestination,
  type LibraryDestinationProps,
} from "./LibraryDestination";
import {
  LibraryPanel,
  type LibraryPanelProps,
  type LibrarySourceFilterCounts,
  type LibrarySourceFilterSlug,
} from "./LibraryPanel";
import {
  SaveToLibraryPopover,
  type SaveToLibraryPopoverProps,
  type SaveToLibrarySubmit,
} from "./SaveToLibraryPopover";

// ===========================================================================
// Re-exports
// ===========================================================================

export { LibraryDestination, type LibraryDestinationProps };

export {
  LibraryPanel,
  type LibraryPanelProps,
  type LibrarySourceFilterCounts,
  type LibrarySourceFilterSlug,
};

export {
  SaveToLibraryPopover,
  type SaveToLibraryPopoverProps,
  type SaveToLibrarySubmit,
};

// Wire-type re-exports (forwarded from `_library-stub.ts`; the
// orchestrator rewires the stub to `@0x-copilot/api-types` at
// merge time — see `_library-stub.ts` header).
//
// TODO(merge): rewire to "@0x-copilot/api-types"
export type {
  LibraryDatasetSummary,
  LibraryFileKind,
  LibraryFileSummary,
  LibraryIndexStatus,
  LibraryItemKind,
  LibraryItemSummary,
  LibraryKindFilterCounts,
  LibraryKindFilterSlug,
  LibraryPageSummary,
  LibrarySortSlug,
  LibrarySource,
  LibrarySourceKind,
  LibraryViewMode,
  SaveToLibraryDefaultKind,
  SaveToLibrarySource,
} from "./_library-stub";

// === P7-B2 detail + preview + page editor ===
export {
  LibraryDetailView,
  type LibraryDetailViewProps,
  type LibraryDetailItem,
  type LibraryDetailItemId,
  type LibraryDetailKind,
  type LibraryDetailIndexStatus,
  type LibraryDetailSource,
  type LibraryDetailSourceKind,
  type LibraryDetailProjectChip,
  type LibraryDetailAuditEntry,
  type LibraryDetailCrossRefs,
  type LibraryFileDetailItem,
  type LibraryPageDetailItem,
  type LibraryDatasetDetailItem,
} from "./LibraryDetailView";
export {
  FilePreview,
  type FilePreviewProps,
  type FilePreviewState,
  type FilePreviewKind,
} from "./preview/FilePreview";
export { PagePreview, type PagePreviewProps } from "./preview/PagePreview";
export {
  DatasetPreview,
  type DatasetPreviewProps,
  type DatasetPreviewState,
  type DatasetColumnSpec,
  type DatasetColumnType,
  type DatasetRow,
} from "./preview/DatasetPreview";
export {
  PageEditor,
  type PageEditorProps,
  type PageEditorSaveStatus,
  type PageEditorView,
} from "./PageEditor";
