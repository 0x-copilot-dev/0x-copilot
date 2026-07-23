// Projects destination — public surface + ItemRef resolver registration.
//
// Per cross-audit §1.1 + §3.3 (binding 2026-05-17), each destination
// registers its kind on package import. Projects owns the resolver for
// kind `"project"` so every other destination's
// `<ItemLink kind="project" id=…>` resolves without forcing a circular
// dependency.
//
// Wire-type re-exports come from the canonical `@0x-copilot/api-types`
// Projects contract.

import {
  ProjectsDestination,
  type ProjectsDestinationProps,
  type ProjectsFilterCounts,
  type ProjectsFilterSlug,
  type RenderProjectDetailSlot,
} from "./ProjectsDestination";
import {
  ProjectFilterChip,
  type ProjectFilterChipOption,
  type ProjectFilterChipProps,
} from "./ProjectFilterChip";

// ===========================================================================
// Re-exports (P6-B1 shell + P6-B2 detail + P6-A1 wire types)
// ===========================================================================

export {
  ProjectsDestination,
  type ProjectsDestinationProps,
  type ProjectsFilterCounts,
  type ProjectsFilterSlug,
  type RenderProjectDetailSlot,
};

export {
  ProjectFilterChip,
  type ProjectFilterChipOption,
  type ProjectFilterChipProps,
};

export {
  ProjectDetailView,
  ProjectFilesTab,
  type ProjectDetail,
  type ProjectDetailProfile,
  type ProjectDetailViewProps,
  type ProjectDetailTabId,
  type ProjectFileRow,
  type ProjectFilesResult,
  type ProjectStatus,
} from "./ProjectDetailView";

export {
  ProjectMembersTab,
  type ProjectMembersTabProps,
  type ProjectMember,
  type ProjectMemberRole,
} from "./ProjectMembersTab";

export {
  ProjectActivityTab,
  type ProjectActivityTabProps,
  type ProjectActivity,
  type ProjectActivityItemRef,
} from "./ProjectActivityTab";

export {
  TransferOwnershipDialog,
  type TransferOwnershipDialogProps,
} from "./transfer-ownership-dialog";

// ===========================================================================
// Phase 6.5 extensions (project editor + templates + archive-blocked UI)
// ===========================================================================

export {
  ProjectEditor,
  type ProjectEditorProps,
  type ProjectEditorTabId,
  type ProjectEditorValue,
  type ProjectEditorSavePayload,
  type ProjectEditorConnectorOption,
  type ProjectEditorConnectorSlug,
  type ProjectConnectorAllowlistMode,
} from "./ProjectEditor";

export {
  TemplateGallery,
  type TemplateGalleryProps,
  type TemplateGalleryFilterSlug,
  type TemplateGalleryFilterCounts,
  type ProjectTemplateCard,
  type ProjectTemplateId,
} from "./TemplateGallery";

export {
  TemplateEditor,
  type TemplateEditorProps,
  type TemplateEditorValue,
  type TemplateEditorSavePayload,
  type TemplateEditorSnapshot,
  type TemplateEditorSeededTodo,
  type TemplateEditorSeededRoutine,
} from "./TemplateEditor";

export {
  ForkFromTemplateDialog,
  type ForkFromTemplateDialogProps,
  type ForkFromTemplateSnapshotSummary,
} from "./fork-from-template-dialog";

export {
  ArchiveBlockedDialog,
  type ArchiveBlockedDialogProps,
  type LivenessDetail,
  type LivenessDetailSource,
  type LivenessReport,
} from "./archive-blocked-dialog";

// Project-name cache — the host binder primes it from the loaded project
// list so the `kind: "project"` ItemRef resolver (below) surfaces the real
// project name instead of the generic "Project" label (FR-G.6).
export {
  cacheProjectName,
  cacheProjectNames,
  getCachedProjectName,
} from "./projectNameCache";

// Wire-type re-exports — canonical Projects contract from
// `@0x-copilot/api-types` (`packages/api-types/src/projects.ts`).
export type {
  ProjectActivityCounts,
  ProjectColorHue,
  ProjectIconEmoji,
  ProjectRole,
  ProjectSummary,
} from "@0x-copilot/api-types";

// ItemRef ROUTE registration moved to the host tables (PRD-04 Seam B). The
// project name is now supplied by the caller via `<ItemLink label={…}>`; the
// `projectNameCache` re-exported above is an ordinary call-site helper for the
// `project_id`-only sites that hold no name (PRD-04 Non-goals), not a resolver.
