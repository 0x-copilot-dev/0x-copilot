import type { ProjectId } from "@0x-copilot/api-types";
import { afterEach, describe, expect, it } from "vitest";

import { hasItemRefResolver, resolveItemRef } from "../../refs/registry";

// Importing the destination's index runs its `registerIfAbsent(...)`
// calls as a side-effect; the test asserts on the post-import state of
// the registry. The registry is a module-singleton, so this load is
// idempotent across the suite (the projects registration guards itself
// with `hasItemRefResolver`).
import "./index";
import {
  cacheProjectName,
  __resetProjectNameCacheForTests,
} from "./projectNameCache";

describe("projects/index.ts — ItemRef resolver registration", () => {
  afterEach(() => {
    __resetProjectNameCacheForTests();
  });

  it("registers a resolver for kind `project`", () => {
    expect(hasItemRefResolver("project")).toBe(true);
  });

  it("falls back to the generic 'Project' label on a cache miss", async () => {
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

  it("surfaces the real project name once the cache is primed (FR-G.6)", async () => {
    cacheProjectName("proj_abc", "Q3 Launch");
    const resolved = await resolveItemRef({
      kind: "project",
      id: "proj_abc" as ProjectId,
    });
    expect(resolved).not.toBeNull();
    expect(resolved!.label).toBe("Q3 Launch");
    // Route + breadcrumb are unchanged by the name.
    expect(resolved!.route).toMatchObject({
      kind: "workspace",
      workspaceId: "proj_abc",
    });
    expect(resolved!.breadcrumb).toBe("Projects");
  });
});
