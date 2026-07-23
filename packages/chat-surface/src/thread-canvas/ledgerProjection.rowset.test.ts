// Client ledger fold — the PRD-D3 bulk row-set peer projection.
//
// Verifies `projectLedger` folds `write.staged {rows, agent_holds}` /
// `revision.added {rowset}` / row-scoped `decision.recorded` / row-set
// `write.applied` into `stages` with the same machine as the Python
// `StagedWriteFold`: rows + stances + counts; agent pre-hold reason sticky across
// a user override; apply freeze → apply_pending; applied / partial / failed
// terminals; re-fold determinism.

import { describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import { projectLedger } from "./ledgerProjection";

const RUN = "run_bulk_v2";
const STAGE = "stage_bulk";

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

function staged(
  rows: number,
  holds: { row_key: string; reason: string }[] = [],
) {
  return ev("write.staged", {
    v: 1,
    stage_id: STAGE,
    surface_id: "surf_bulk",
    target: { connector: "linear", op: "update_issue" },
    proposal_ref: `stage://${STAGE}/v1`,
    rows,
    agent_holds: holds,
  });
}

function rowsetRev(keys: string[]) {
  return ev("revision.added", {
    v: 1,
    stage_id: STAGE,
    rev: 1,
    author: "agent",
    diff_ref: `stage://${STAGE}/v1`,
    proposal_ref: `stage://${STAGE}/v1`,
    rowset: {
      rows: keys.map((k) => ({
        row_key: k,
        title: `Row ${k}`,
        target_args: { id: k, priority: 2 },
        changes: [{ field: "priority", old: 1, new: 2 }],
      })),
    },
  });
}

function rowDecision(
  decision: string,
  keys: string[],
  actor = "user",
): RuntimeEventEnvelope {
  return ev("decision.recorded", {
    v: 1,
    stage_id: STAGE,
    decision,
    scope: { row_keys: keys },
    actor,
  });
}

function applyDecision(keys: string[], actor = "user"): RuntimeEventEnvelope {
  return ev("decision.recorded", {
    v: 1,
    stage_id: STAGE,
    decision: "approve",
    scope: { row_keys: keys },
    actor,
    apply: true,
  });
}

function applied(
  result: string,
  keys: string[],
  rowResults: { row_key: string; outcome: string }[],
): RuntimeEventEnvelope {
  return ev("write.applied", {
    v: 1,
    stage_id: STAGE,
    rev: 1,
    result,
    row_keys: keys,
    row_results: rowResults,
  });
}

describe("projectLedger — bulk row-sets (PRD-D3)", () => {
  it("folds rows, default stances, and counts", () => {
    seq = 0;
    const p = projectLedger([staged(3), rowsetRev(["a", "b", "c"])]);
    const s = p.stages.get(STAGE)!;
    expect(s.rows!.map((r) => r.rowKey)).toEqual(["a", "b", "c"]);
    expect(s.rows!.every((r) => r.stance === "will_apply")).toBe(true);
    expect(s.rowCounts).toEqual({
      total: 3,
      willApply: 3,
      held: 0,
      applied: 0,
      failed: 0,
    });
  });

  it("keeps the agent pre-hold reason sticky across a user override", () => {
    seq = 0;
    const events = [
      staged(3, [{ row_key: "b", reason: "recent reply" }]),
      rowsetRev(["a", "b", "c"]),
    ];
    let s = projectLedger(events).stages.get(STAGE)!;
    let b = s.rows!.find((r) => r.rowKey === "b")!;
    expect(b.stance).toBe("held");
    expect(b.agentHoldReason).toBe("recent reply");
    expect(b.decidedBy).toBe("agent");

    s = projectLedger([...events, rowDecision("approve", ["b"])]).stages.get(
      STAGE,
    )!;
    b = s.rows!.find((r) => r.rowKey === "b")!;
    expect(b.stance).toBe("will_apply");
    expect(b.agentHoldReason).toBe("recent reply"); // STICKY
    expect(b.decidedBy).toBe("user");
    expect(s.rowCounts!.willApply).toBe(3);
  });

  it("freezes to apply_pending on the apply decision", () => {
    seq = 0;
    const p = projectLedger([
      staged(2),
      rowsetRev(["a", "b"]),
      applyDecision(["a", "b"]),
    ]);
    expect(p.stages.get(STAGE)!.status).toBe("apply_pending");
  });

  it("folds an applied terminal with per-row outcomes", () => {
    seq = 0;
    const p = projectLedger([
      staged(2),
      rowsetRev(["a", "b"]),
      applyDecision(["a", "b"]),
      applied(
        "applied",
        ["a", "b"],
        [
          { row_key: "a", outcome: "applied" },
          { row_key: "b", outcome: "applied" },
        ],
      ),
    ]);
    const s = p.stages.get(STAGE)!;
    expect(s.status).toBe("applied");
    expect(s.rowCounts!.applied).toBe(2);
  });

  it("folds a partial terminal (partially_applied + per-row outcomes)", () => {
    seq = 0;
    const p = projectLedger([
      staged(2),
      rowsetRev(["a", "b"]),
      applyDecision(["a", "b"]),
      applied(
        "partial",
        ["a", "b"],
        [
          { row_key: "a", outcome: "applied" },
          { row_key: "b", outcome: "failed" },
        ],
      ),
    ]);
    const s = p.stages.get(STAGE)!;
    expect(s.status).toBe("partially_applied");
    expect(s.rowCounts!.applied).toBe(1);
    expect(s.rowCounts!.failed).toBe(1);
    expect(s.rows!.find((r) => r.rowKey === "b")!.applyOutcome).toBe("failed");
  });

  it("returns to staged (apply consumed) on an all-failed terminal", () => {
    seq = 0;
    const p = projectLedger([
      staged(2),
      rowsetRev(["a", "b"]),
      applyDecision(["a", "b"]),
      applied("failed", ["a", "b"], []),
    ]);
    const s = p.stages.get(STAGE)!;
    expect(s.status).toBe("staged");
    expect(s.rowCounts!.willApply).toBe(2);
  });

  it("marks a policy-actor auto-apply (allow-always)", () => {
    seq = 0;
    const p = projectLedger([
      staged(2),
      rowsetRev(["a", "b"]),
      applyDecision(["a", "b"], "policy"),
    ]);
    const s = p.stages.get(STAGE)!;
    expect(s.status).toBe("apply_pending");
    const applyDec = s.decisions.find((d) => d.actor === "policy");
    expect(applyDec).toBeDefined();
  });

  it("re-folds identically regardless of event order", () => {
    seq = 0;
    const events = [
      staged(3, [{ row_key: "c", reason: "unsure" }]),
      rowsetRev(["a", "b", "c"]),
      rowDecision("approve", ["c"]),
      applyDecision(["a", "b", "c"]),
      applied(
        "applied",
        ["a", "b", "c"],
        [
          { row_key: "a", outcome: "applied" },
          { row_key: "b", outcome: "applied" },
          { row_key: "c", outcome: "applied" },
        ],
      ),
    ];
    const first = projectLedger(events).stages.get(STAGE);
    const second = projectLedger([...events].reverse()).stages.get(STAGE);
    expect(first).toEqual(second);
  });
});
