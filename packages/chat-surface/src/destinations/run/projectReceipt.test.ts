// projectReceipt tests (Generative Surfaces v2, PRD-E1 DoD).
//
// The hard gate: the TS `foldReceipt` of PRD-A1's golden Work Ledger events MUST
// byte-equal the shared `work_ledger_expected_receipt.json` fixture — the SAME
// referee the Python `ReceiptFold` regenerates. Both fixtures are imported
// directly from disk (the `adapterAllowlist` precedent), so this fails if either
// language drifts. Plus: the receipt is null until `receipt.emitted`; malformed
// payloads are skipped; and two decision paths yield two different receipts.

import { describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

// A1 golden events (shared fold input) + E1 expected receipt (the referee).
import goldenEvents from "../../../../service-contracts/src/copilot_service_contracts/work_ledger_golden_events.json";
import expectedReceipt from "../../../../service-contracts/src/copilot_service_contracts/work_ledger_expected_receipt.json";

import { foldReceipt, projectReceipt } from "./projectReceipt";

const events = (goldenEvents as { events: unknown[] })
  .events as unknown as RuntimeEventEnvelope[];

const RUN = "run00000001abcdef";

function ev(
  event_type: string,
  sequence_no: number,
  payload: unknown,
): RuntimeEventEnvelope {
  return {
    event_type,
    run_id: RUN,
    sequence_no,
    created_at: `2026-01-01T00:00:${String(sequence_no).padStart(2, "0")}Z`,
    payload,
  } as unknown as RuntimeEventEnvelope;
}

describe("projectReceipt ↔ Python ReceiptFold parity (PRD-E1 DoD item 1)", () => {
  it("ts fold of the golden events byte-equals the py expected receipt", () => {
    expect(foldReceipt(events)).toEqual(expectedReceipt);
  });

  it("re-folding the golden events is deep-equal (idempotent fold)", () => {
    expect(foldReceipt(events)).toEqual(foldReceipt(events));
  });

  it("projectReceipt surfaces the folded receipt once receipt.emitted is seen", () => {
    const projection = projectReceipt(events);
    expect(projection.receipt).toEqual(expectedReceipt);
    expect(projection.emittedSeq).toBe(20);
  });
});

describe("projectReceipt live gate", () => {
  it("returns receipt: null until a receipt.emitted event lands", () => {
    const withoutEmit = [
      ev("read.executed", 1, {
        v: 1,
        call_id: "c1",
        connector: "linear",
        op: "get_issue",
        latency_ms: 10,
        payload_ref: "call:c1",
      }),
    ];
    const projection = projectReceipt(withoutEmit);
    expect(projection.receipt).toBeNull();
    expect(projection.emittedSeq).toBeNull();
  });
});

describe("projectReceipt session accuracy (PRD-E1 DoD item 2)", () => {
  const prefix = [
    ev("read.executed", 1, {
      v: 1,
      call_id: "c1",
      connector: "linear",
      op: "get_issue",
      latency_ms: 10,
      payload_ref: "call:c1",
    }),
    ev("surface.created", 2, {
      v: 1,
      surface_id: "s1",
      kind: "record",
      source: { connector: "linear", op: "get_issue" },
      title: "ENG-1",
      payload_ref: "p/s1",
    }),
    ev("write.staged", 3, {
      v: 1,
      stage_id: "st1",
      surface_id: "s1",
      target: { connector: "linear", op: "update_issue" },
      proposal_ref: "draft://abcdef0123456789abcdef0123456789/v1",
      rows: 2,
    }),
    ev("receipt.emitted", 9, {
      v: 1,
      surface_id: "receipt://x",
      fold_ref: "y",
    }),
  ];

  it("approve→applied vs reject produce different, correct receipts", () => {
    const approve = [
      ...prefix,
      ev("decision.recorded", 4, {
        v: 1,
        stage_id: "st1",
        decision: "approve",
        scope: { rev: 1 },
        actor: "user",
      }),
      ev("write.applied", 5, {
        v: 1,
        stage_id: "st1",
        rev: 1,
        result: "applied",
        row_keys: ["a", "b"],
      }),
    ];
    const reject = [
      ...prefix,
      ev("decision.recorded", 4, {
        v: 1,
        stage_id: "st1",
        decision: "reject",
        scope: { rev: 1 },
        actor: "user",
      }),
    ];
    const a = projectReceipt(approve).receipt!;
    const b = projectReceipt(reject).receipt!;

    expect(a.tiles.writes_approved).toBe(2);
    expect(a.tiles.holds_untouched).toBe(0);
    expect(b.tiles.writes_approved).toBe(0);
    expect(b.tiles.holds_untouched).toBe(2);

    const aAttribs = a.rows.map((r) => r.attribution);
    const bAttribs = b.rows.map((r) => r.attribution);
    expect(aAttribs).toContain("approved");
    expect(aAttribs).not.toContain("rejected");
    expect(bAttribs).toContain("rejected");
    expect(bAttribs).toContain("held");
    expect(a).not.toEqual(b);
  });

  it("policy approve renders auto_applied", () => {
    const policy = [
      ...prefix,
      ev("decision.recorded", 4, {
        v: 1,
        stage_id: "st1",
        decision: "approve",
        scope: { rev: 1 },
        actor: "policy",
      }),
      ev("write.applied", 5, {
        v: 1,
        stage_id: "st1",
        rev: 1,
        result: "applied",
        row_keys: ["a", "b"],
      }),
    ];
    const receipt = projectReceipt(policy).receipt!;
    const applyRow = receipt.rows.find((r) => r.event_type === "write.applied");
    expect(applyRow?.attribution).toBe("auto_applied");
  });
});

describe("projectReceipt is total (adversarial)", () => {
  it("skips malformed / unknown events without throwing", () => {
    const noisy = [
      ev("read.executed", 1, {
        v: 1,
        call_id: "c1",
        connector: "linear",
        op: "get_issue",
        latency_ms: 5,
        payload_ref: "call:c1",
      }),
      ev("totally.unknown", 2, null),
      ev("write.staged", 3, "not-a-dict"),
      ev("receipt.emitted", 4, {
        v: 1,
        surface_id: "receipt://x",
        fold_ref: "y",
      }),
    ];
    const receipt = projectReceipt(noisy).receipt!;
    expect(receipt.tiles.reads_auto_ran).toBe(1);
    expect(receipt.tiles.writes_proposed).toBe(0);
  });
});
