// Todos destination — public surface + ItemRef resolver registration.
//
// Per cross-audit §1.1 + §3.3, each destination registers its kind on
// package import. Todos owns the resolver for kind `"todo"` so that
// every other destination's `<ItemLink kind="todo" id=…>` resolves
// without forcing a circular dependency.
//
// Wire-type re-exports are forwarded from `_todos-stub.ts`; the
// orchestrator rewires the stub to `@0x-copilot/api-types`
// at merge time.

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
// rewires the stub to `@0x-copilot/api-types` at merge — see
// `_todos-stub.ts` header).
//
// TODO(merge): rewire to "@0x-copilot/api-types"
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
