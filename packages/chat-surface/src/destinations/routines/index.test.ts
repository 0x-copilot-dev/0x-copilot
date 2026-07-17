import type { RoutineId } from "@0x-copilot/api-types";
import { describe, expect, it } from "vitest";

import { hasItemRefResolver, resolveItemRef } from "../../refs/registry";

// Importing the destination's index runs its `registerIfAbsent(...)`
// calls as a side-effect; the test asserts on the post-import state of
// the registry. The registry is a module-singleton, so this load is
// idempotent across the suite (the routines registration guards itself
// with `hasItemRefResolver`).
import "./index";

describe("routines/index.ts — ItemRef resolver registration", () => {
  it("registers a resolver for kind `routine`", () => {
    expect(hasItemRefResolver("routine")).toBe(true);
  });

  it("resolves a routine ref to a non-null route + 'Routine' label", async () => {
    const resolved = await resolveItemRef({
      kind: "routine",
      id: "rt_abc" as RoutineId,
    });
    expect(resolved).not.toBeNull();
    expect(resolved!.label).toBe("Routine");
    // P5-B3 will introduce a dedicated `{ kind: "routine-detail",
    // routineId }` route variant and replace this resolver. Until then
    // the workspace route is the stable fallback so <ItemLink> renders
    // a real link rather than the deleted-chip.
    expect(resolved!.route).not.toBeNull();
    expect(resolved!.route).toMatchObject({
      kind: "workspace",
      workspaceId: "rt_abc",
    });
    expect(resolved!.breadcrumb).toBe("Routines");
  });
});
