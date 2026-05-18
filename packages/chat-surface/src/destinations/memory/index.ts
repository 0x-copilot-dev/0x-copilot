// Memory destination — public surface + ItemRef resolver registration.
//
// Per cross-audit §1.1 + §3.3 (binding 2026-05-17), each destination
// registers its kind on package import. Memory owns the resolver for
// kind `"memory"` so every other destination's
// `<ItemLink kind="memory" id=…>` resolves without forcing a circular
// dependency.
//
// Wire types live in `@enterprise-search/api-types/memory` (shipped on
// main via P12-A1) — this module re-exports them so consumers can grab
// `MemoryItem` / `MemoryProposal` / etc. from a single chat-surface
// surface alongside the components.

import type { MemoryItemId } from "@enterprise-search/api-types";

import {
  hasItemRefResolver,
  registerItemRefResolver,
} from "../../refs/registry";

import {
  MemoryDestination,
  type MemoryDestinationProps,
  type MemoryKindFilterCounts,
  type MemoryKindFilterSlug,
  type MemoryScopeFilterSlug,
  type RenderMemoryDetailSlot,
} from "./MemoryDestination";
import {
  MemoryDetailView,
  type MemoryDetailTabSlug,
  type MemoryDetailViewProps,
} from "./MemoryDetailView";
import {
  MemoryEditor,
  type MemoryEditorProps,
  type MemoryEditorSavePayload,
} from "./MemoryEditor";
import {
  MemoryPanel,
  type MemoryPanelProps,
  type MemoryPanelTagChip,
} from "./MemoryPanel";
import {
  MemoryProposalCard,
  type MemoryProposalCardProps,
} from "./MemoryProposalCard";
import {
  MemoryProposalToast,
  MemoryProposalToastStack,
  type MemoryProposalToastProps,
  type MemoryProposalToastStackProps,
} from "./MemoryProposalToast";

// ===========================================================================
// Re-exports — components
// ===========================================================================

export {
  MemoryDestination,
  type MemoryDestinationProps,
  type MemoryKindFilterCounts,
  type MemoryKindFilterSlug,
  type MemoryScopeFilterSlug,
  type RenderMemoryDetailSlot,
};

export { MemoryPanel, type MemoryPanelProps, type MemoryPanelTagChip };

export {
  MemoryDetailView,
  type MemoryDetailTabSlug,
  type MemoryDetailViewProps,
};

export { MemoryEditor, type MemoryEditorProps, type MemoryEditorSavePayload };

export {
  MemoryProposalToast,
  MemoryProposalToastStack,
  type MemoryProposalToastProps,
  type MemoryProposalToastStackProps,
};

export { MemoryProposalCard, type MemoryProposalCardProps };

// ===========================================================================
// Re-exports — wire types (forwarded from api-types so consumers can
// import the full surface from a single chat-surface module).
// ===========================================================================

export type {
  AcceptMemoryProposalRequest,
  CreateMemoryRequest,
  MemoryCreator,
  MemoryCreatorKind,
  MemoryItem,
  MemoryKind,
  MemoryListFilterAxis,
  MemoryListResponse,
  MemoryListSort,
  MemoryProposal,
  MemoryProposalDecisionStatus,
  MemoryProposalListResponse,
  MemoryScope,
  MemorySearchHit,
  MemorySearchResponse,
  MemoryStreamEnvelope,
  MemoryStreamEventType,
  UpdateMemoryRequest,
} from "@enterprise-search/api-types";

// ===========================================================================
// ItemRef resolver registration (cross-audit §3.3)
// ===========================================================================
//
// Until the host defines a dedicated `{ kind: "memory-detail",
// memoryId }` route variant, the workspace route is the stable fallback
// so `<ItemLink kind="memory">` renders a real link rather than the
// deleted-chip. Mirrors the routines/inbox bootstrap.

if (!hasItemRefResolver("memory")) {
  registerItemRefResolver("memory", async (id: MemoryItemId) => ({
    label: "Memory",
    icon: null,
    route: { kind: "workspace", workspaceId: id as unknown as string },
    breadcrumb: "Memory",
  }));
}
