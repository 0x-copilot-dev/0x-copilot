// Tools destination — public surface + ItemRef resolver registration.
//
// Per cross-audit §1.1 + §3.3 (binding 2026-05-17), each destination
// registers its kind on package import. Tools owns the resolver for
// kind `"tool"` so every other destination's
// `<ItemLink kind="tool" id=…>` resolves without a circular dep.
//
// Three building blocks (P10-B1):
//   - ToolsDestination — catalog shell (header + filter tabs + grid)
//   - ToolsPanel       — left rail (kind / scope / status chips + search)
//   - ToolCard         — single tool row in the grid

import type { ToolId } from "@0x-copilot/api-types";

import {
  hasItemRefResolver,
  registerItemRefResolver,
} from "../../refs/registry";

import { ToolCard, type ToolCardProps } from "./ToolCard";
import {
  ToolsDestination,
  type ToolsDestinationProps,
} from "./ToolsDestination";
import { ToolsPanel, type ToolsPanelProps } from "./ToolsPanel";

// ===========================================================================
// Re-exports
// ===========================================================================

export { ToolsDestination, type ToolsDestinationProps };
export { ToolsPanel, type ToolsPanelProps };
export { ToolCard, type ToolCardProps };

export {
  ONBOARD_KIND_TILES,
  TOOLS_FILTER_LABELS,
  TOOLS_FILTER_ORDER,
  TOOLS_KIND_LABELS,
  TOOLS_KIND_ORDER,
  TOOLS_SCOPE_LABELS,
  TOOLS_SCOPE_ORDER,
  TOOLS_SORT_LABELS,
  TOOLS_SORT_ORDER,
  TOOLS_STATUS_LABELS,
  filterTools,
  isInstalled,
  searchTools,
  sortTools,
  statusTone,
  type KindOnboardTile,
  type Tool,
  type ToolKind,
  type ToolScope,
  type ToolStatus,
  type ToolsFilterContext,
  type ToolsFilterSlug,
  type ToolsSortSlug,
} from "./_tools-stub";

// ===========================================================================
// ItemRef resolver registration (cross-audit §3.3)
// ===========================================================================
//
// `<ItemLink ref={{ kind: "tool", id }}>` from any destination resolves
// here. The detail view lands in P10-B2 with a dedicated `{ kind:
// "tool-detail", toolId }` route variant; until then, the workspace
// route is the stable fallback so the link renders as a real link
// rather than the deleted-chip. Same pattern Library + Projects use.

if (!hasItemRefResolver("tool")) {
  registerItemRefResolver("tool", async (id: ToolId) => ({
    label: "Tool",
    icon: null,
    route: { kind: "workspace", workspaceId: id as unknown as string },
    breadcrumb: "Tools",
  }));
}
