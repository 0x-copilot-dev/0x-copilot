// Team destination — ItemRef resolver registration.

import type { UserId } from "@0x-copilot/api-types";
import { describe, expect, it } from "vitest";

import { hasItemRefResolver, resolveItemRef } from "../../refs/registry";

// Importing the destination's index runs its `registerIfAbsent(...)`
// calls as a side-effect; the test asserts on the post-import state.
import "./index";

describe("team/index.ts — ItemRef resolver registration", () => {
  it("registers a resolver for kind `person`", () => {
    expect(hasItemRefResolver("person")).toBe(true);
  });

  it("resolves a person ref to a non-null route + 'Person' label", async () => {
    const resolved = await resolveItemRef({
      kind: "person",
      id: "u_abc" as UserId,
    });
    expect(resolved).not.toBeNull();
    expect(resolved!.label).toBe("Person");
    expect(resolved!.route).toMatchObject({
      kind: "workspace",
      workspaceId: "u_abc",
    });
    expect(resolved!.breadcrumb).toBe("Team");
  });
});
