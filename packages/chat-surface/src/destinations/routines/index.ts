// Routines destination — public surface + ItemRef resolver registration.
//
// Per cross-audit §1.1 + §3.3 (binding 2026-05-17), each destination
// registers its kind on package import. Routines owns the resolver for
// kind `"routine"` so every other destination's
// `<ItemLink kind="routine" id=…>` resolves without forcing a circular
// dependency.
//
// Wire-type re-exports are forwarded from `_routines-stub.ts`; the
// orchestrator rewires the stub to `@enterprise-search/api-types` at
// merge time.

import type { RoutineId } from "@enterprise-search/api-types";

import {
  hasItemRefResolver,
  registerItemRefResolver,
} from "../../refs/registry";

import {
  RoutinesDestination,
  nextFireDisplay,
  uniqueTriggerKinds,
  type RenderRoutineDetailSlot,
  type RoutinesDestinationProps,
  type RoutinesFilterCounts,
  type RoutinesFilterSlug,
} from "./RoutinesDestination";
import {
  RoutinesPanel,
  type RoutinesPanelProjectChip,
  type RoutinesPanelProps,
  type RoutinesPanelTriggerCounts,
  type RoutinesPanelTriggerSlug,
} from "./RoutinesPanel";

// ===========================================================================
// Re-exports
// ===========================================================================

export {
  RoutinesDestination,
  nextFireDisplay,
  uniqueTriggerKinds,
  type RenderRoutineDetailSlot,
  type RoutinesDestinationProps,
  type RoutinesFilterCounts,
  type RoutinesFilterSlug,
};

export {
  RoutinesPanel,
  type RoutinesPanelProjectChip,
  type RoutinesPanelProps,
  type RoutinesPanelTriggerCounts,
  type RoutinesPanelTriggerSlug,
};

// Wire-type re-exports (forwarded from `_routines-stub.ts`; the
// orchestrator rewires the stub to `@enterprise-search/api-types` at
// merge time — see `_routines-stub.ts` header).
//
// TODO(merge): rewire to "@enterprise-search/api-types"
export type {
  Routine,
  RoutineAutonomy,
  RoutineBehavior,
  RoutineConnectorConfig,
  RoutineDataResidency,
  RoutineManualFire,
  RoutineMissedFirePolicy,
  RoutineOutputTarget,
  RoutinePermissions,
  RoutineScope,
  RoutineStatus,
  RoutineTrigger,
  RoutineTriggerKind,
  TriggerId,
} from "./_routines-stub";

// ===========================================================================
// ItemRef resolver registration (cross-audit §3.3)
// ===========================================================================
//
// P5-B3 will introduce a dedicated `{ kind: "routine-detail",
// routineId }` route variant. Until then, the workspace route is the
// stable fallback so `<ItemLink kind="routine">` renders a real link
// rather than the deleted-chip.

if (!hasItemRefResolver("routine")) {
  registerItemRefResolver("routine", async (id: RoutineId) => ({
    label: "Routine",
    icon: null,
    route: { kind: "workspace", workspaceId: id as unknown as string },
    breadcrumb: "Routines",
  }));
}
