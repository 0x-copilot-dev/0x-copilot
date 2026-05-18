// Projects destination — public surface + ItemRef resolver registration.
//
// Per cross-audit §1.1 + §3.3 (binding 2026-05-17), each destination
// registers its kind on package import. Projects owns the resolver for
// kind `"project"` so every other destination's
// `<ItemLink kind="project" id=…>` resolves without forcing a circular
// dependency.
//
// Wire-type re-exports are forwarded from `_projects-stub.ts`; the
// orchestrator rewires the stub to `@enterprise-search/api-types` at
// merge time.

import type { ProjectId } from "@enterprise-search/api-types";

import {
  hasItemRefResolver,
  registerItemRefResolver,
} from "../../refs/registry";

import {
  ProjectsDestination,
  type ProjectsDestinationProps,
  type ProjectsFilterCounts,
  type ProjectsFilterSlug,
  type RenderProjectDetailSlot,
} from "./ProjectsDestination";
import { ProjectsPanel, type ProjectsPanelProps } from "./ProjectsPanel";
import {
  ProjectFilterChip,
  type ProjectFilterChipOption,
  type ProjectFilterChipProps,
} from "./ProjectFilterChip";

// ===========================================================================
// Re-exports
// ===========================================================================

export {
  ProjectsDestination,
  type ProjectsDestinationProps,
  type ProjectsFilterCounts,
  type ProjectsFilterSlug,
  type RenderProjectDetailSlot,
};

export { ProjectsPanel, type ProjectsPanelProps };

export {
  ProjectFilterChip,
  type ProjectFilterChipOption,
  type ProjectFilterChipProps,
};

// Wire-type re-exports (forwarded from `_projects-stub.ts`; the
// orchestrator rewires the stub to `@enterprise-search/api-types` at
// merge time — see `_projects-stub.ts` header).
//
// TODO(merge): rewire to "@enterprise-search/api-types"
export type {
  ProjectActivityCounts,
  ProjectColorHue,
  ProjectIconEmoji,
  ProjectRole,
  ProjectStatus,
  ProjectSummary,
} from "./_projects-stub";

// ===========================================================================
// ItemRef resolver registration (cross-audit §3.3)
// ===========================================================================
//
// P6-B2 will introduce a dedicated `{ kind: "project-detail",
// projectId }` route variant. Until then, the workspace route is the
// stable fallback so `<ItemLink kind="project">` renders a real link
// rather than the deleted-chip.

if (!hasItemRefResolver("project")) {
  registerItemRefResolver("project", async (id: ProjectId) => ({
    label: "Project",
    icon: null,
    route: { kind: "workspace", workspaceId: id as unknown as string },
    breadcrumb: "Projects",
  }));
}
