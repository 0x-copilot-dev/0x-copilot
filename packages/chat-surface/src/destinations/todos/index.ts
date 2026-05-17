// Todos destination — public surface + ItemRef resolver registration.
//
// Per cross-audit §1.1 + §3.3, each destination registers its kind on
// package import. Todos owns the resolver for kind `"todo"` so that
// every other destination's `<ItemLink kind="todo" id=…>` resolves
// without forcing a circular dependency.
//
// Wire-type re-exports are forwarded from `_todos-stub.ts`; the
// orchestrator rewires the stub to `@enterprise-search/api-types`
// at merge time.

import type { TodoId } from "@enterprise-search/api-types";

import {
  hasItemRefResolver,
  registerItemRefResolver,
} from "../../refs/registry";

import {
  TodosDestination,
  type TodosDestinationProps,
  type InlineAddSlot,
  type ExtractionBannerSlot,
  type RecurrenceEditorSlot,
  type SubtaskTreeSlot,
} from "./TodosDestination";
import {
  TodosPanel,
  type TodosPanelProps,
  type TodosFilterSlug,
  type TodosProjectChip,
  type TodosSavedFilter,
} from "./TodosPanel";

// ===========================================================================
// Re-exports
// ===========================================================================

export {
  TodosDestination,
  type TodosDestinationProps,
  type InlineAddSlot,
  type ExtractionBannerSlot,
  type RecurrenceEditorSlot,
  type SubtaskTreeSlot,
};
export {
  TodosPanel,
  type TodosPanelProps,
  type TodosFilterSlug,
  type TodosProjectChip,
  type TodosSavedFilter,
};

// Wire-type re-exports (forwarded from `_todos-stub.ts`; the orchestrator
// rewires the stub to `@enterprise-search/api-types` at merge — see
// `_todos-stub.ts` header).
//
// TODO(merge): rewire to "@enterprise-search/api-types"
export type {
  Todo,
  TodoExtraction,
  TodoPriority,
  TodoSectionKey,
  TodoSource,
  TodosPayload,
} from "./TodosDestination";

// Bucketing helper — exported so `apps/frontend` (P3-C) can compute the
// badge count from the same client-side bucketing rule the shell uses.
export { bucketTodos } from "./TodosDestination";

// ===========================================================================
// ItemRef resolver registration (cross-audit §3.3)
// ===========================================================================
//
// Guarded with `hasItemRefResolver` to keep test environments — which
// may import the module in multiple realms / vitest workers — from
// throwing `ItemRefResolverAlreadyRegistered`. The host's richer
// resolver (with denormalized title/excerpt) replaces this later with
// `{ replace: true }`.
//
// Phase-3 minimal resolver: the route target is the workspace surface
// for the todo's id. P3-C is the agent that owns the route extension
// (an `ArtifactRoute` variant `{ kind: "todo", todoId }` may land —
// until then we route to the existing workspace destination so the
// chip is at least clickable rather than dead).

if (!hasItemRefResolver("todo")) {
  registerItemRefResolver("todo", async (id: TodoId) => ({
    label: "Todo",
    icon: null,
    // P3-C will introduce `{ kind: "todo", todoId }` to ArtifactRoute and
    // re-register with `{ replace: true }`. Until then route to the
    // workspace surface keyed by the todo id — that gives a stable,
    // non-null target so `<ItemLink>` renders a real link instead of
    // the deleted-chip fallback.
    route: { kind: "workspace", workspaceId: id as unknown as string },
    breadcrumb: "Todos",
  }));
}
