import { describe, expect, it } from "vitest";

import { WEB_WORKSPACE_STAGE_HOST } from "./webWorkspaceStageHost";

describe("WEB_WORKSPACE_STAGE_HOST", () => {
  it("declares browser-only review authority without a local workspace capability", () => {
    expect(WEB_WORKSPACE_STAGE_HOST).toEqual({ kind: "web" });
    expect(Object.isFrozen(WEB_WORKSPACE_STAGE_HOST)).toBe(true);
    const serialized = JSON.stringify(WEB_WORKSPACE_STAGE_HOST);
    expect(serialized).not.toMatch(/approvalPort|path|permit|prepared/i);
  });
});
