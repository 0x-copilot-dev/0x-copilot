import type { ProjectId } from "@enterprise-search/api-types";
import { describe, expect, it } from "vitest";

import { hasItemRefResolver, resolveItemRef } from "../../refs/registry";

// Importing the destination's index runs its `registerIfAbsent(...)`
// calls as a side-effect; the test asserts on the post-import state of
// the registry. The registry is a module-singleton, so this load is
// idempotent across the suite (the projects registration guards itself
// with `hasItemRefResolver`).
import "./index";

describe("projects/index.ts — ItemRef resolver registration", () => {
  it("registers a resolver for kind `project`", () => {
    expect(hasItemRefResolver("project")).toBe(true);
  });

  it("resolves a project ref to a non-null route + 'Project' label", async () => {
    const resolved = await resolveItemRef({
      kind: "project",
      id: "proj_abc" as ProjectId,
    });
    expect(resolved).not.toBeNull();
    expect(resolved!.label).toBe("Project");
    // P6-B2 will introduce a dedicated `{ kind: "project-detail",
    // projectId }` route variant and replace this resolver. Until then
    // the workspace route is the stable fallback so <ItemLink> renders
    // a real link rather than the deleted-chip.
    expect(resolved!.route).not.toBeNull();
    expect(resolved!.route).toMatchObject({
      kind: "workspace",
      workspaceId: "proj_abc",
    });
    expect(resolved!.breadcrumb).toBe("Projects");
  });
});
