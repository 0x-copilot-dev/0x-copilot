// Routines destination — public surface + ItemRef resolver registration.
//
// Per cross-audit §1.1 + §3.3 (binding 2026-05-17), each destination
// registers its kind on package import. Routines owns the resolver for
// kind `"routine"` so every other destination's
// `<ItemLink kind="routine" id=…>` resolves without forcing a circular
// dependency.
//
// Wire-type re-exports are forwarded from `_routines-stub.ts`; the
// orchestrator rewires the stub to `@0x-copilot/api-types` at
// merge time.

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
// orchestrator rewires the stub to `@0x-copilot/api-types` at
// merge time — see `_routines-stub.ts` header).
//
// TODO(merge): rewire to "@0x-copilot/api-types"
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
