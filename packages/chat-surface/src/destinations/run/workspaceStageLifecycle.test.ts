import { describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import { projectWorkspaceStageLifecycle } from "./workspaceStageLifecycle";

const RUN_ID = "run_workspace_1";
const STAGE_ID = "stage_workspace_1";
const ARTIFACT_ID = "artifact_workspace_1";
const PROPOSAL_DIGEST = "a".repeat(64);
const TARGET_DIGEST = "b".repeat(64);

function event(
  sequenceNo: number,
  eventType: string,
  payload: Record<string, unknown>,
): RuntimeEventEnvelope {
  return {
    event_id: `evt_workspace_${sequenceNo}`,
    run_id: RUN_ID,
    conversation_id: "conv_workspace_1",
    sequence_no: sequenceNo,
    event_type: eventType,
    activity_kind: "event",
    payload,
    created_at: new Date(1_700_000_000_000 + sequenceNo * 1_000).toISOString(),
  } as unknown as RuntimeEventEnvelope;
}

function staged(overrides: Record<string, unknown> = {}): RuntimeEventEnvelope {
  return event(3, "effect.staged", {
    v: 1,
    stage_id: STAGE_ID,
    operation_id: "operation_workspace_1",
    executor: "workspace",
    target_ref: "opaque-target-ref",
    target_digest: TARGET_DIGEST,
    proposal_ref: "opaque-proposal-ref",
    proposal_digest: PROPOSAL_DIGEST,
    proposal_content_ref: "content-workspace-r2",
    policy: "ask",
    op: "create_file",
    display_target: "/workspace/Finance/report.csv",
    author_actor: "user",
    ...overrides,
  });
}

describe("projectWorkspaceStageLifecycle", () => {
  it("projects a canonical workspace effect into a safe digest-pinned Studio review", () => {
    const reviews = projectWorkspaceStageLifecycle([
      event(1, "artifact.created", {
        v: 1,
        artifact_id: ARTIFACT_ID,
        revision: 1,
        kind: "dataset",
        content_ref: "content-workspace-r1",
      }),
      // `artifact.revised` deliberately omits kind: the projection must retain
      // the previously established safe artifact kind instead of guessing.
      event(2, "artifact.revised", {
        v: 1,
        artifact_id: ARTIFACT_ID,
        revision: 2,
        content_ref: "content-workspace-r2",
      }),
      staged(),
    ]);

    const review = reviews.get(STAGE_ID);
    expect(review).toBeDefined();
    expect(review?.stage).toMatchObject({
      stageId: STAGE_ID,
      title: "Create workspace file",
      operation: { kind: "create" },
      target: {
        mountLabel: "Workspace",
        virtualPath: "/workspace/Finance/report.csv",
      },
      status: "staged",
      decisionAvailable: true,
      restoreAvailable: false,
      editAvailable: true,
    });
    expect(review?.snapshot).toEqual({
      runId: RUN_ID,
      stageId: STAGE_ID,
      revision: 1,
      proposalDigest: PROPOSAL_DIGEST,
      targetDigest: TARGET_DIGEST,
    });
    expect(review?.artifactFallback).toEqual({
      artifactId: ARTIFACT_ID,
      revision: 2,
      kind: "dataset",
    });

    // The presenter consumes opaque refs internally only to correlate an
    // already-authorised artifact; none of them escape to UI/IPC state.
    const renderedState = JSON.stringify(review);
    expect(renderedState).not.toContain("opaque-target-ref");
    expect(renderedState).not.toContain("opaque-proposal-ref");
    expect(renderedState).not.toContain("content-workspace-r2");
    expect(renderedState).not.toContain("permit");
    expect(renderedState).not.toContain("prepared");
  });

  it("holds unsafe, unknown, and stale stages without offering a generic write", () => {
    const unsafe = projectWorkspaceStageLifecycle([
      staged({
        display_target: "/Users/alice/secret.csv",
      }),
    ]).get(STAGE_ID);
    expect(unsafe?.stage.status).toBe("held");
    expect(unsafe?.stage.resolution).toEqual({ state: "details_unavailable" });
    expect(unsafe?.stage.target.virtualPath).toBe("");
    expect(unsafe?.snapshot).toBeNull();

    const smuggledRef = projectWorkspaceStageLifecycle([
      staged({ display_target: "proposal_ref: workspace-prepared://private" }),
    ]).get(STAGE_ID);
    expect(smuggledRef?.stage.target.mountLabel).toBe("Workspace");
    expect(JSON.stringify(smuggledRef)).not.toContain("workspace-prepared");

    const stale = projectWorkspaceStageLifecycle([
      staged(),
      event(4, "effect.decision_recorded", {
        v: 1,
        stage_id: STAGE_ID,
        revision: 2,
        decision: "approve",
        actor: "user",
        proposal_digest: PROPOSAL_DIGEST,
        target_digest: TARGET_DIGEST,
      }),
    ]).get(STAGE_ID);
    expect(stale?.stage.status).toBe("held");
    expect(stale?.stage.resolution).toEqual({ state: "details_unavailable" });
    expect(stale?.snapshot).toBeNull();
  });

  it("ignores non-workspace effect stages rather than rendering a workspace authority card", () => {
    const reviews = projectWorkspaceStageLifecycle([
      staged({ executor: "mcp" }),
    ]);
    expect(reviews.size).toBe(0);
  });

  it("retains an already-authorised artifact fallback across a revision that omits its content ref", () => {
    const review = projectWorkspaceStageLifecycle([
      event(1, "artifact.created", {
        v: 1,
        artifact_id: ARTIFACT_ID,
        revision: 1,
        kind: "dataset",
        content_ref: "content-workspace-r2",
      }),
      staged(),
      event(4, "effect.revised", {
        v: 1,
        stage_id: STAGE_ID,
        revision: 2,
        proposal_ref: "opaque-proposal-ref-r2",
        proposal_digest: PROPOSAL_DIGEST,
      }),
    ]).get(STAGE_ID);

    expect(review?.snapshot?.revision).toBe(2);
    expect(review?.artifactFallback).toEqual({
      artifactId: ARTIFACT_ID,
      revision: 1,
      kind: "dataset",
    });
  });
});
