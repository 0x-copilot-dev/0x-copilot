import { describe, expect, it } from "vitest";

import type { PendingWorkItemV2 } from "@0x-copilot/api-types";

import {
  pendingWorkStatusLabelV2,
  pendingWorkSubjectLabelV2,
  projectPendingWorkV2,
} from "./pendingWorkV2Projection";

function item(over: Partial<PendingWorkItemV2> = {}): PendingWorkItemV2 {
  return {
    run_id: "run_a",
    subject_kind: "effect",
    subject_id: "stage_a",
    status: "held",
    opened_sequence_no: 3,
    latest_sequence_no: 3,
    ...over,
  };
}

describe("projectPendingWorkV2", () => {
  it("dedupes by run + opaque subject and retains server order", () => {
    const cards = projectPendingWorkV2([
      item({ run_id: "run_b", subject_id: "stage_b", opened_sequence_no: 4 }),
      item({ run_id: "run_a", subject_id: "stage_a", opened_sequence_no: 2 }),
      // A fresher duplicate updates state in-place without jumping ahead.
      item({
        run_id: "run_b",
        subject_id: "stage_b",
        status: "approved",
        opened_sequence_no: 4,
        latest_sequence_no: 8,
      }),
      item({
        run_id: "run_c",
        subject_kind: "gate",
        subject_id: "workspace:op_123",
        status: "open",
        opened_sequence_no: 1,
        latest_sequence_no: 1,
      }),
    ]);

    expect(cards.map((card) => `${card.runId}/${card.subjectId}`)).toEqual([
      "run_b/stage_b",
      "run_a/stage_a",
      "run_c/workspace:op_123",
    ]);
    expect(cards[0]?.status).toBe("approved");
    expect(cards[0]?.latestSeq).toBe(8);
  });

  it("uses controlled copy for every API enum", () => {
    expect(pendingWorkSubjectLabelV2("effect")).toBe("PROPOSED CHANGE");
    expect(pendingWorkSubjectLabelV2("gate")).toBe("ACCESS NEEDED");
    expect(pendingWorkStatusLabelV2("recovery")).toBe("Needs recovery");
    expect(pendingWorkStatusLabelV2("approved")).toBe(
      "Approved, waiting to apply",
    );
  });
});
