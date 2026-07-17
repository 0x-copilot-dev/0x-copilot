import type { TodoId } from "@0x-copilot/api-types";
import { describe, expect, it } from "vitest";

import { hasItemRefResolver, resolveItemRef } from "../../refs/registry";

// Importing the destination's index runs its `registerIfAbsent(...)`
// calls as a side-effect; the test asserts on the post-import state of
// the registry. The registry is a module-singleton, so this load is
// idempotent across the suite (the todos registration guards itself
// with `hasItemRefResolver`).
import "./index";

describe("todos/index.ts — ItemRef resolver registration", () => {
  it("registers a resolver for kind `todo`", () => {
    expect(hasItemRefResolver("todo")).toBe(true);
  });

  it("resolves a todo ref to a non-null route + a 'Todo' label", async () => {
    const resolved = await resolveItemRef({
      kind: "todo",
      id: "todo_abc" as TodoId,
    });
    expect(resolved).not.toBeNull();
    expect(resolved!.label).toBe("Todo");
    // P3-C will introduce a dedicated `{ kind: "todo", todoId }` route
    // variant and replace this resolver. Until then the workspace route
    // is the stable fallback so <ItemLink> renders a real link rather
    // than the deleted-chip.
    expect(resolved!.route).not.toBeNull();
    expect(resolved!.route).toMatchObject({
      kind: "workspace",
      workspaceId: "todo_abc",
    });
    expect(resolved!.breadcrumb).toBe("Todos");
  });
});
