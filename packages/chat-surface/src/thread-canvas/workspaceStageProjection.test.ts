import { describe, expect, it } from "vitest";

import {
  WORKSPACE_STAGE_PLEDGE,
  isDestructiveWorkspaceOperation,
  projectWorkspaceStage,
  safeWorkspaceDigest,
  safeWorkspaceVirtualPath,
  workspaceStageOperationLabel,
  type WorkspaceStage,
  type WorkspaceStageOperationKind,
} from "./workspaceStageProjection";

const HASH = "a".repeat(64);

function stage(overrides: Partial<WorkspaceStage> = {}): WorkspaceStage {
  return {
    stageId: "workspace-stage-1",
    title: "Create Finance report",
    operation: { kind: "create" },
    target: {
      mountLabel: "Finance workspace",
      virtualPath: "/workspace/Finance/report.csv",
    },
    revision: 3,
    author: "Agent",
    status: "staged",
    preview: null,
    diff: null,
    baseline: null,
    precondition: null,
    revisionHistory: null,
    resolution: null,
    ...overrides,
  };
}

describe("workspace stage display projection", () => {
  it.each<readonly [WorkspaceStageOperationKind, boolean, string]>([
    ["create", false, "create"],
    ["replace", false, "replace"],
    ["delete", true, "delete"],
    ["move", false, "move"],
    ["mkdir", false, "mkdir"],
  ])("projects the %s operation", (kind, destructive, label) => {
    const operation = { kind } as WorkspaceStage["operation"];
    expect(isDestructiveWorkspaceOperation(operation)).toBe(destructive);
    expect(workspaceStageOperationLabel(operation)).toBe(label);
  });

  it("treats an overwrite move as destructive", () => {
    const operation = { kind: "move" as const, overwrite: true };
    expect(isDestructiveWorkspaceOperation(operation)).toBe(true);
    expect(workspaceStageOperationLabel(operation)).toBe("move · overwrite");
  });

  it("only exposes virtual /workspace paths and SHA-256 digests", () => {
    expect(safeWorkspaceVirtualPath("/workspace/Finance/report.csv")).toBe(
      "/workspace/Finance/report.csv",
    );
    expect(safeWorkspaceVirtualPath("/Users/alice/report.csv")).toBeNull();
    expect(safeWorkspaceVirtualPath("C:\\Users\\alice\\report.csv")).toBeNull();
    expect(safeWorkspaceVirtualPath("/workspace/../private.txt")).toBeNull();
    expect(safeWorkspaceDigest(HASH)).toBe(HASH);
    expect(safeWorkspaceDigest("permit-token-should-not-render")).toBeNull();
  });

  it("fails closed on physical labels and sensitive summary fields", () => {
    const projected = projectWorkspaceStage(
      stage({
        target: {
          mountLabel: "file:///Users/alice/Finance",
          virtualPath: "/Users/alice/Finance/report.csv",
        },
        baseline: {
          summary: "physical_path=/Users/alice/Finance/report.csv",
          digest: "permit-token-should-not-render",
        },
        resolution: {
          state: "precondition_drift",
          summary: "permit_token=permit-secret-value",
        },
      }),
    );

    expect(projected.mountLabel).toBe("Workspace");
    expect(projected.virtualPath).toBeNull();
    expect(projected.baselineSummary).toBe("The target must remain absent.");
    expect(projected.baselineDigest).toBeNull();
    expect(projected.resolutionSummary).toContain("Nothing was applied");
  });

  it("uses compact Focus projection and blocks decisions during reconciliation", () => {
    const projected = projectWorkspaceStage(
      stage({ resolution: { state: "reconciling" } }),
      "focus",
    );

    expect(projected.compact).toBe(true);
    expect(projected.canDecide).toBe(false);
    expect(projected.resolutionLabel).toBe("Reconciling");
    expect(projected.canEdit).toBe(false);
  });

  it("does not offer a decision when the target cannot be safely reviewed", () => {
    const projected = projectWorkspaceStage(
      stage({
        target: {
          mountLabel: "Finance workspace",
          virtualPath: "/Users/alice/Finance/report.csv",
        },
      }),
    );

    expect(projected.canDecide).toBe(false);
  });

  it("pins the exact C3 approval pledge", () => {
    expect(WORKSPACE_STAGE_PLEDGE).toBe(
      "Only this revision and target will be applied.",
    );
  });
});
