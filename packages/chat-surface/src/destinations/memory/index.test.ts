import type { MemoryItemId } from "@enterprise-search/api-types";
import { describe, expect, it } from "vitest";

import { hasItemRefResolver, resolveItemRef } from "../../refs/registry";

// Importing the destination's index runs its `registerIfAbsent(...)`
// calls as a side-effect. The registry is a module-singleton, so this
// load is idempotent across the suite (the memory registration guards
// itself with `hasItemRefResolver`).
import "./index";

describe("memory/index.ts — ItemRef resolver registration", () => {
  it("registers a resolver for kind `memory`", () => {
    expect(hasItemRefResolver("memory")).toBe(true);
  });

  it("resolves a memory ref to a non-null route + 'Memory' label", async () => {
    const resolved = await resolveItemRef({
      kind: "memory",
      id: "mem_abc" as MemoryItemId,
    });
    expect(resolved).not.toBeNull();
    expect(resolved!.label).toBe("Memory");
    // Until the host defines a dedicated `{ kind: "memory-detail",
    // memoryId }` route variant, the workspace route is the stable
    // fallback so `<ItemLink kind="memory">` renders a real link
    // rather than the deleted-chip.
    expect(resolved!.route).not.toBeNull();
    expect(resolved!.route).toMatchObject({
      kind: "workspace",
      workspaceId: "mem_abc",
    });
    expect(resolved!.breadcrumb).toBe("Memory");
  });
});
