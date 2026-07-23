// @vitest-environment node
import { describe, expect, it } from "vitest";

import { ACTIVITY_RUN_STATUSES, type ActivityRunRow } from "./activity";
import { ACTIVE_AGENT_RUN_STATUSES, AGENT_RUN_STATUSES } from "./index";
import type { RunId } from "./brands";

// Runtime assertions over the Activity run-history contract (desktop
// redesign, Phase 4). The status tuple is the runtime SSOT the
// `ActivityRunStatus` union derives from — pinning it guards against
// silent union drift, and `needs_input` is load-bearing (it is how the
// former Inbox items surface as Activity rows, FR-4.15/4.18/4.33).

describe("ActivityRunStatus — run row status union", () => {
  it("is exactly running / done / paused / stopped / needs_input, in order", () => {
    expect([...ACTIVITY_RUN_STATUSES]).toEqual([
      "running",
      "done",
      "paused",
      "stopped",
      "needs_input",
    ]);
  });

  it("includes needs_input (folded-in Inbox items)", () => {
    expect(ACTIVITY_RUN_STATUSES).toContain("needs_input");
  });

  it("has no duplicate members", () => {
    expect(new Set(ACTIVITY_RUN_STATUSES).size).toBe(
      ACTIVITY_RUN_STATUSES.length,
    );
  });
});

describe("ACTIVE_AGENT_RUN_STATUSES — the emittable latest_run_status subset (PRD-05)", () => {
  it("is exactly the four non-terminal statuses, in order", () => {
    expect(ACTIVE_AGENT_RUN_STATUSES).toHaveLength(4);
    expect([...ACTIVE_AGENT_RUN_STATUSES]).toEqual([
      "queued",
      "running",
      "waiting_for_approval",
      "cancelling",
    ]);
  });

  it("is a subset of the full AGENT_RUN_STATUSES union", () => {
    for (const status of ACTIVE_AGENT_RUN_STATUSES) {
      expect(AGENT_RUN_STATUSES).toContain(status);
    }
  });
});

describe("ActivityRunRow — shape", () => {
  const row: ActivityRunRow = {
    run_id: "run_001" as RunId,
    title: "Reconcile invoices",
    status: "running",
    meta: "Stripe · Google Drive",
    started_at: "2026-07-18T09:15:00Z",
  };

  it("carries exactly the run-row fields", () => {
    expect(Object.keys(row).sort()).toEqual(
      ["meta", "run_id", "started_at", "status", "title"].sort(),
    );
  });

  it("uses a status drawn from the status tuple", () => {
    expect(ACTIVITY_RUN_STATUSES).toContain(row.status);
  });
});
