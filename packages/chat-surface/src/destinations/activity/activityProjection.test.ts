// @vitest-environment node
import type { RunHistoryEntry } from "@0x-copilot/api-types";
import { describe, expect, it } from "vitest";

import { mapRunStatus, projectActivityRows } from "./activityProjection";

// Minimal RunHistoryEntry fixture — fills the non-load-bearing required fields
// so the tests state only the fields the projection reads (PRD-08 D1).
function entry(over: Partial<RunHistoryEntry> = {}): RunHistoryEntry {
  return {
    run_id: "run_1",
    conversation_id: "conv_1",
    conversation_title: "Weekly treasury reconciliation",
    status: "running",
    model_name: "gpt-4o",
    created_at: "2026-07-18T08:00:00Z",
    started_at: "2026-07-18T09:15:00Z",
    completed_at: null,
    cancelled_at: null,
    connector_count: null,
    step_count: null,
    pending_approval_count: 0,
    ...over,
  };
}

describe("projectActivityRows — RunHistoryEntry[] → ActivityRunRow[] (PRD-08 D1)", () => {
  // conversation_id and run_id are DISTINCT fields, both stamped.
  it("stamps conversation_id and run_id as distinct fields", () => {
    const rows = projectActivityRows([
      entry({ conversation_id: "conv_abc", run_id: "run_xyz" }),
    ]);
    expect(rows).toHaveLength(1);
    expect(rows[0]!.conversation_id).toBe("conv_abc");
    expect(rows[0]!.run_id).toBe("run_xyz");
    expect(rows[0]!.conversation_id).not.toBe(rows[0]!.run_id);
  });

  it("falls back to 'Untitled run' when the conversation title is blank/whitespace/null", () => {
    expect(
      projectActivityRows([entry({ conversation_title: "   " })])[0]!.title,
    ).toBe("Untitled run");
    expect(
      projectActivityRows([entry({ conversation_title: null })])[0]!.title,
    ).toBe("Untitled run");
  });

  it("uses the trimmed conversation title when present", () => {
    const rows = projectActivityRows([
      entry({ conversation_title: "  Draft investor update  " }),
    ]);
    expect(rows[0]!.title).toBe("Draft investor update");
  });

  it("maps the runtime run status onto the Activity taxonomy", () => {
    expect(
      projectActivityRows([entry({ status: "waiting_for_approval" })])[0]!
        .status,
    ).toBe("needs_input");
    expect(
      projectActivityRows([entry({ status: "completed" })])[0]!.status,
    ).toBe("done");
    expect(
      projectActivityRows([entry({ status: "timed_out" })])[0]!.status,
    ).toBe("stopped");
  });

  it("composes the meta line from the server counters (one composer)", () => {
    const rows = projectActivityRows([
      entry({
        connector_count: 4,
        step_count: 7,
        pending_approval_count: 1,
      }),
    ]);
    expect(rows[0]!.meta).toBe("4 apps · 7 steps · awaiting 1 approval");
  });

  it("renders an empty meta line when the counters are unknown/zero", () => {
    const rows = projectActivityRows([
      entry({
        connector_count: null,
        step_count: null,
        pending_approval_count: 0,
      }),
    ]);
    expect(rows[0]!.meta).toBe("");
  });

  it("uses started_at for row time, falling back to created_at when a run is unstarted", () => {
    expect(
      projectActivityRows([
        entry({
          started_at: "2026-07-18T09:15:00Z",
          created_at: "2026-07-18T08:00:00Z",
        }),
      ])[0]!.started_at,
    ).toBe("2026-07-18T09:15:00Z");
    expect(
      projectActivityRows([
        entry({ started_at: null, created_at: "2026-07-18T08:00:00Z" }),
      ])[0]!.started_at,
    ).toBe("2026-07-18T08:00:00Z");
  });

  it("preserves the server's newest-first order (endpoint is ordered; shell re-buckets)", () => {
    const rows = projectActivityRows([
      entry({ run_id: "r_new", conversation_id: "c_new" }),
      entry({ run_id: "r_old", conversation_id: "c_old" }),
    ]);
    expect(rows.map((r) => r.conversation_id)).toEqual(["c_new", "c_old"]);
  });
});

describe("mapRunStatus — total 8→4 fold (PRD-08 D2)", () => {
  it("folds every AgentRunStatus into the four-value taxonomy", () => {
    expect(mapRunStatus("running")).toBe("running");
    expect(mapRunStatus("queued")).toBe("running");
    expect(mapRunStatus("cancelling")).toBe("running");
    expect(mapRunStatus("completed")).toBe("done");
    expect(mapRunStatus("waiting_for_approval")).toBe("needs_input");
    expect(mapRunStatus("cancelled")).toBe("stopped");
    expect(mapRunStatus("failed")).toBe("stopped");
    expect(mapRunStatus("timed_out")).toBe("stopped");
  });
});
