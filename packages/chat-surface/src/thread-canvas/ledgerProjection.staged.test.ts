// Client ledger fold — the PRD-D1 staged-write peer projection.
//
// Verifies `projectLedger` folds `write.staged` / `revision.added` /
// `decision.recorded` into `stages` with the same status machine as the Python
// `StagedWriteFold`: rev bump, approve pins latest, reject → rejected, restore →
// staged, and re-fold determinism. `write.applied` is never produced by D1, so
// the fold's `applied` status is exercised only as forward-compat here.

import { describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import { projectLedger } from "./ledgerProjection";

const RUN = "run_launch_v2";

let seq = 0;
function ev(
  eventType: string,
  payload: Record<string, unknown>,
): RuntimeEventEnvelope {
  seq += 1;
  return {
    event_id: `evt_${seq}`,
    run_id: RUN,
    sequence_no: seq,
    event_type: eventType,
    source: "runtime",
    payload,
  } as unknown as RuntimeEventEnvelope;
}

function stageEvents(stageId: string, draftId: string): RuntimeEventEnvelope[] {
  return [
    ev("surface.created", {
      v: 1,
      surface_id: "surf_1",
      kind: "message",
      source: { connector: "gmail", op: "send" },
      title: "Launch email",
      payload_ref: `draft://${draftId}/v1`,
    }),
    ev("write.staged", {
      v: 1,
      stage_id: stageId,
      surface_id: "surf_1",
      target: { connector: "gmail", op: "send" },
      proposal_ref: `draft://${draftId}/v1`,
    }),
    ev("revision.added", {
      v: 1,
      stage_id: stageId,
      rev: 1,
      author: "agent",
      diff_ref: `draft://${draftId}/v1..v1`,
      proposal_ref: `draft://${draftId}/v1`,
      authorship_spans: [],
    }),
  ];
}

describe("projectLedger — staged writes (PRD-D1)", () => {
  it("folds write.staged + revision.added rev 1 into a staged stage", () => {
    seq = 0;
    const p = projectLedger(stageEvents("stage_1", "draft_1"));
    const stage = p.stages.get("stage_1");
    expect(stage).toBeDefined();
    expect(stage!.status).toBe("staged");
    expect(stage!.latestRev).toBe(1);
    expect(stage!.draftId).toBe("draft_1");
    expect(stage!.target).toEqual({ connector: "gmail", op: "send" });
    expect(stage!.revisions[0].author).toBe("agent");
    expect(stage!.revisions[0].authorshipSpans).toEqual([]);
    expect(stage!.latestRevision!.rev).toBe(1);
    expect(stage!.ledgerId.startsWith("r")).toBe(true);
  });

  it("bumps latestRev on a user revision and keeps its authorship spans", () => {
    seq = 0;
    const events = [
      ...stageEvents("stage_1", "draft_1"),
      ev("revision.added", {
        v: 1,
        stage_id: "stage_1",
        rev: 2,
        author: "user",
        diff_ref: "draft://draft_1/v1..v2",
        proposal_ref: "draft://draft_1/v2",
        authorship_spans: [{ start: 21, end: 24, author: "user" }],
      }),
    ];
    const stage = projectLedger(events).stages.get("stage_1")!;
    expect(stage.latestRev).toBe(2);
    expect(stage.latestRevision!.rev).toBe(2);
    expect(stage.latestRevision!.author).toBe("user");
    expect(stage.revisions[1].authorshipSpans).toEqual([
      { start: 21, end: 24, author: "user" },
    ]);
  });

  it("approve pins the scoped rev and flips status to approved", () => {
    seq = 0;
    const events = [
      ...stageEvents("stage_1", "draft_1"),
      ev("decision.recorded", {
        v: 1,
        stage_id: "stage_1",
        decision: "approve",
        scope: { rev: 1 },
        actor: "user",
      }),
    ];
    const stage = projectLedger(events).stages.get("stage_1")!;
    expect(stage.status).toBe("approved");
    expect(stage.approvedRev).toBe(1);
    // No write.applied was folded — nothing executed.
    expect(
      [...projectLedger(events).stages.values()].every(
        (s) => s.status !== "applied",
      ),
    ).toBe(true);
  });

  it("reject → rejected, then restore → staged (re-pin latest)", () => {
    seq = 0;
    const events = [
      ...stageEvents("stage_1", "draft_1"),
      ev("revision.added", {
        v: 1,
        stage_id: "stage_1",
        rev: 2,
        author: "user",
        diff_ref: "draft://draft_1/v1..v2",
        proposal_ref: "draft://draft_1/v2",
        authorship_spans: [{ start: 0, end: 3, author: "user" }],
      }),
      ev("decision.recorded", {
        v: 1,
        stage_id: "stage_1",
        decision: "reject",
        scope: { rev: 2 },
        actor: "user",
      }),
    ];
    let stage = projectLedger(events).stages.get("stage_1")!;
    expect(stage.status).toBe("rejected");
    expect(stage.approvedRev).toBeNull();

    const restored = [
      ...events,
      ev("decision.recorded", {
        v: 1,
        stage_id: "stage_1",
        decision: "restore",
        scope: { rev: 2 },
        actor: "user",
      }),
    ];
    stage = projectLedger(restored).stages.get("stage_1")!;
    expect(stage.status).toBe("staged");
    expect(stage.latestRev).toBe(2);
  });

  it("ignores a revision/decision for an unseen stage; re-fold is deterministic", () => {
    seq = 0;
    const events = [
      ev("revision.added", {
        v: 1,
        stage_id: "ghost",
        rev: 1,
        author: "user",
        diff_ref: "x",
      }),
      ...stageEvents("stage_1", "draft_1"),
    ];
    const a = projectLedger(events);
    const b = projectLedger(events);
    expect(a.stages.has("ghost")).toBe(false);
    expect(a.stages.get("stage_1")!.status).toBe("staged");
    expect(JSON.stringify([...b.stages.values()])).toBe(
      JSON.stringify([...a.stages.values()]),
    );
  });

  it("tolerates interleaved non-stage events without affecting the fold", () => {
    seq = 0;
    const events = [
      ev("progress", { text: "thinking" }),
      ...stageEvents("stage_1", "draft_1"),
      ev("model_delta", { text: "..." }),
      ev("decision.recorded", {
        v: 1,
        stage_id: "stage_1",
        decision: "approve",
        scope: { rev: 1 },
        actor: "user",
      }),
    ];
    const stage = projectLedger(events).stages.get("stage_1")!;
    expect(stage.status).toBe("approved");
    expect(stage.approvedRev).toBe(1);
  });
});

function approvedEvents(): RuntimeEventEnvelope[] {
  return [
    ...stageEvents("stage_1", "draft_1"),
    ev("decision.recorded", {
      v: 1,
      stage_id: "stage_1",
      decision: "approve",
      scope: { rev: 1 },
      actor: "user",
    }),
  ];
}

describe("projectLedger — write.applied state machine (PRD-D2)", () => {
  it("applied on the approved rev ⇒ APPLIED terminal + applyResult", () => {
    seq = 0;
    const events = [
      ...approvedEvents(),
      ev("write.applied", {
        v: 1,
        stage_id: "stage_1",
        rev: 1,
        result: "applied",
        connector_receipt_ref: "commit://stage_1/4",
        decided_by: { actor: "user", decision_seq: 4 },
      }),
    ];
    const stage = projectLedger(events).stages.get("stage_1")!;
    expect(stage.status).toBe("applied");
    expect(stage.applyResult).toBe("applied");
    expect(stage.applyFailureCode).toBeNull();
    expect(stage.approvedRev).toBe(1);
  });

  it("failed on the approved rev ⇒ back to STAGED (held), approval consumed", () => {
    seq = 0;
    const events = [
      ...approvedEvents(),
      ev("write.applied", {
        v: 1,
        stage_id: "stage_1",
        rev: 1,
        result: "failed",
        failure: { code: "precondition_drift" },
      }),
    ];
    const stage = projectLedger(events).stages.get("stage_1")!;
    // Held: back to staged, approval consumed, failure code surfaced for the UI.
    expect(stage.status).toBe("staged");
    expect(stage.approvedRev).toBeNull();
    expect(stage.applyResult).toBe("failed");
    expect(stage.applyFailureCode).toBe("precondition_drift");
  });

  it("applied on a NON-approved stage ⇒ CORRUPT (fail-closed, not sent)", () => {
    seq = 0;
    const events = [
      ...stageEvents("stage_1", "draft_1"), // never approved
      ev("write.applied", {
        v: 1,
        stage_id: "stage_1",
        rev: 1,
        result: "applied",
      }),
    ];
    const stage = projectLedger(events).stages.get("stage_1")!;
    expect(stage.status).toBe("corrupt");
  });

  it("applied on a mismatched rev ⇒ CORRUPT", () => {
    seq = 0;
    const events = [
      ...approvedEvents(), // approved rev 1
      ev("write.applied", {
        v: 1,
        stage_id: "stage_1",
        rev: 2, // mismatch
        result: "applied",
      }),
    ];
    expect(projectLedger(events).stages.get("stage_1")!.status).toBe("corrupt");
  });

  it("re-fold with applied is deterministic", () => {
    seq = 0;
    const events = [
      ...approvedEvents(),
      ev("write.applied", {
        v: 1,
        stage_id: "stage_1",
        rev: 1,
        result: "applied",
      }),
    ];
    const a = projectLedger(events);
    const b = projectLedger(events);
    expect(JSON.stringify([...a.stages.values()])).toBe(
      JSON.stringify([...b.stages.values()]),
    );
    expect(a.stages.get("stage_1")!.status).toBe("applied");
  });
});
