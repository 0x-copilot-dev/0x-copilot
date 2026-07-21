// Re-export the frozen SurfaceSpec contract (PRD-01) from the single source of
// truth, `@0x-copilot/api-types`, plus a couple of defensive narrowing helpers
// the archetype renderers use to pull a spec / data out of an `unknown`
// boundary value without ever throwing.
//
// All re-exports are type-only, so nothing from api-types survives to runtime —
// the renderers stay a pure, dependency-light leaf under D28.

export type {
  SurfaceArchetype,
  SurfaceColumn,
  SurfaceColumnAlign,
  SurfaceDiff,
  SurfaceEnvelope,
  SurfaceField,
  SurfaceFieldChange,
  SurfaceFieldFormat,
  SurfaceLink,
  SurfaceSource,
  SurfaceSpec,
  SurfaceState,
} from "@0x-copilot/api-types";

import type {
  SurfaceDiff,
  SurfaceFieldChange,
  SurfaceSpec,
  SurfaceState,
} from "@0x-copilot/api-types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Best-effort narrow of an `unknown` boundary value to a `SurfaceSpec`. Returns
 * `undefined` (⇒ spec-less fallback render) unless the value carries the minimal
 * shape of a spec: an object with a string `archetype` and string `title_path`.
 * Never throws.
 */
export function specFromState(state: unknown): SurfaceSpec | undefined {
  if (!isRecord(state)) {
    return undefined;
  }
  const candidate = "spec" in state ? state.spec : state;
  if (!isRecord(candidate)) {
    return undefined;
  }
  if (
    typeof candidate.archetype === "string" &&
    typeof candidate.title_path === "string"
  ) {
    return candidate as unknown as SurfaceSpec;
  }
  return undefined;
}

/**
 * Pull the untrusted tool-output payload out of a surface `state`. Accepts both
 * the `{ spec, data }` envelope shape and a bare data object. Never throws.
 */
export function dataFromState(state: unknown): unknown {
  if (isRecord(state) && "data" in state) {
    return (state as Partial<SurfaceState>).data;
  }
  return state;
}

/** Defensive read of the change list from a `SurfaceDiff`-ish value. */
export function changesFromDiff(diff: unknown): readonly SurfaceFieldChange[] {
  if (isRecord(diff) && Array.isArray((diff as Partial<SurfaceDiff>).changes)) {
    return (diff as unknown as SurfaceDiff).changes.filter(
      (change): change is SurfaceFieldChange =>
        isRecord(change) && typeof change.field === "string",
    );
  }
  return [];
}
