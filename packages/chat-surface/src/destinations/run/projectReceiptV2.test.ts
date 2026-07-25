// Additive D4 Receipt v2 parity and safety tests.
//
// This shared-fixture prefix suite mirrors
// `test_receipt_fold_v2.py`. Both implementations consume the same canonical
// journey data without mounting a receipt surface or opening a canvas.

import { describe, expect, it } from "vitest";

import goldenEvents from "../../../../service-contracts/src/copilot_service_contracts/work_ledger_golden_events.json";
import expectedReceipt from "../../../../service-contracts/src/copilot_service_contracts/work_ledger_expected_receipt.json";
import goldenJourneys from "../../../../service-contracts/src/copilot_service_contracts/work_ledger_v2_1_golden_journeys.json";

import {
  foldReceiptV2,
  projectReceiptV2,
  type ReceiptV2EventLike,
} from "./projectReceiptV2";

interface ExpectedReceiptFacts {
  readonly operations: {
    readonly requested: number;
    readonly succeeded: number;
    readonly staged: number;
    readonly blocked: number;
    readonly cancelled: number;
    readonly failed: number;
  };
  readonly effects: {
    readonly staged: number;
    readonly applied: number;
    readonly partial: number;
    readonly failed: number;
    readonly cancelled: number;
    readonly indeterminate: number;
    readonly already_applied: number;
    readonly precondition_drift: number;
  };
  readonly gates: { readonly opened: number; readonly resolved: number };
}

interface Journey {
  readonly id: string;
  readonly events: readonly (ReceiptV2EventLike & {
    readonly run_id: string;
  })[];
  readonly expected: { readonly receipt: ExpectedReceiptFacts };
}

const journeys = (goldenJourneys as { readonly journeys: readonly Journey[] })
  .journeys;

function journey(id: string): Journey {
  const value = journeys.find((candidate) => candidate.id === id);
  if (value === undefined) throw new Error(`missing journey: ${id}`);
  return value;
}

describe("Receipt v2 shared-fixture prefix parity", () => {
  it("is total and idempotent for every canonical golden-journey prefix", () => {
    for (const item of journeys) {
      const runId = item.events[0]?.run_id;
      expect(runId, item.id).toBeTypeOf("string");
      if (runId === undefined) continue;
      for (
        let prefixLength = 0;
        prefixLength <= item.events.length;
        prefixLength += 1
      ) {
        const prefix = item.events.slice(0, prefixLength);
        expect(foldReceiptV2(runId, prefix, "completed"), item.id).toEqual(
          foldReceiptV2(runId, prefix, "completed"),
        );
      }
    }
  });

  it("keeps the canonical fixture's operation/effect/gate facts", () => {
    for (const item of journeys) {
      const runId = item.events[0]?.run_id;
      if (runId === undefined) continue;
      const receipt = foldReceiptV2(runId, item.events, "completed");
      const expected = item.expected.receipt;
      expect(receipt.operations).toEqual({
        requested: expected.operations.requested,
        completed:
          expected.operations.succeeded +
          expected.operations.staged +
          expected.operations.blocked +
          expected.operations.cancelled,
        failed: expected.operations.failed,
        blocked: expected.operations.blocked,
      });
      expect(receipt.effects.proposed).toBe(expected.effects.staged);
      expect(receipt.effects.applied).toBe(
        expected.effects.applied + expected.effects.already_applied,
      );
      expect(receipt.effects.partial).toBe(expected.effects.partial);
      expect(receipt.effects.indeterminate).toBe(
        expected.effects.indeterminate,
      );
      expect(receipt.gates).toMatchObject(expected.gates);
    }
  });
});

describe("Receipt v2 compatibility and distinction", () => {
  it("retains the old expected-receipt fixture as read-only compatibility facts", () => {
    const legacy = goldenEvents as {
      readonly run_id: string;
      readonly events: readonly ReceiptV2EventLike[];
    };
    const old = expectedReceipt as {
      readonly fold_ref: string;
      readonly tiles: { readonly reads_auto_ran: number };
    };
    const receipt = foldReceiptV2(legacy.run_id, legacy.events, "completed");

    expect(receipt.fold_ref).toBe(old.fold_ref);
    expect(receipt.reads.completed).toBe(old.tiles.reads_auto_ran);
    expect(receipt.effects.proposed).toBeGreaterThan(0);
    expect(receipt.effects.external).toBe(receipt.effects.proposed);
  });

  it("keeps internal artifact events separate from external effect stages", () => {
    const artifact = journey("model_authored_code_artifact");
    const effect = journey("workspace_commit_success");
    const artifactReceipt = foldReceiptV2(
      artifact.events[0]!.run_id,
      artifact.events,
      "completed",
    );
    const effectReceipt = foldReceiptV2(
      effect.events[0]!.run_id,
      effect.events,
      "completed",
    );

    expect(artifactReceipt.artifacts.created).toBe(1);
    expect(artifactReceipt.effects.external).toBe(0);
    expect(effectReceipt.artifacts.created).toBe(0);
    expect(effectReceipt.effects.external).toBe(1);
    expect(effectReceipt.effects.applied).toBe(1);
  });
});

describe("Receipt v2 chat-only availability", () => {
  it("keeps a zero-operation chat receipt conditional and never auto-opens", () => {
    const running = projectReceiptV2("run00000001abcdef", [], "running");
    expect(running).toEqual({
      receipt: null,
      available: false,
      chatOnly: true,
      shouldAutoOpen: false,
    });

    const completed = projectReceiptV2("run00000001abcdef", [], "completed");
    expect(completed.available).toBe(true);
    expect(completed.chatOnly).toBe(true);
    expect(completed.shouldAutoOpen).toBe(false);
    expect(completed.receipt).toMatchObject({
      status: "completed",
      operations: { requested: 0, completed: 0, failed: 0, blocked: 0 },
    });
  });
});

describe("Receipt v2 malformed-event safety", () => {
  it("is total and omits paths, bodies, and secrets from the projection", () => {
    const secret = "sk-never-copy-this";
    const path = "/Users/alice/private.txt";
    const receipt = foldReceiptV2(
      "run00000001abcdef",
      [
        {
          event_type: "usage.recorded",
          sequence_no: 1,
          created_at: "2026-07-25T00:00:01Z",
          payload: {
            v: 1,
            purpose: "run",
            model: secret,
            tokens_in: 3,
            tokens_out: 5,
          },
        },
        {
          event_type: "effect.staged",
          sequence_no: 2,
          created_at: "2026-07-25T00:00:02Z",
          payload: {
            v: 1,
            stage_id: "stg_018f47a6-7b2c-7c10-8f21-12345678c001",
            operation_id: "op_018f47a6-7b2c-7a10-8f21-12345678a001",
            executor: "workspace",
            target_ref: `file://${path}`,
            target_digest: "a".repeat(64),
            proposal_ref:
              "proposal://stg_018f47a6-7b2c-7c10-8f21-12345678c001/1",
            proposal_digest: "b".repeat(64),
            policy: "require",
            body: secret,
          },
        },
        { event_type: "unknown.event", sequence_no: 3, payload: secret },
      ],
      "completed",
    );

    expect(receipt.usage.totals_by_purpose).toEqual([
      { purpose: "run", records: 1, tokens_in: 3, tokens_out: 5 },
    ]);
    expect(receipt.usage.references).toHaveLength(1);
    expect(receipt.effects.proposed).toBe(0);
    expect(receipt.unresolved_warnings).toContainEqual({
      code: "malformed_events",
      count: 1,
    });
    const rendered = JSON.stringify(receipt);
    expect(rendered).not.toContain(secret);
    expect(rendered).not.toContain(path);
    expect(rendered).not.toContain("file://");
  });
});
