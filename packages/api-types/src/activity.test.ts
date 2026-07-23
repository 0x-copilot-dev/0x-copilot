// @vitest-environment node
import { describe, expect, it } from "vitest";

import { ACTIVITY_RUN_STATUSES, type ActivityRunRow } from "./activity";
import { ACTIVE_AGENT_RUN_STATUSES, AGENT_RUN_STATUSES } from "./index";
import type { ConversationId, RunId } from "./brands";

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
    conversation_id: "conv_001" as ConversationId,
    title: "Reconcile invoices",
    status: "running",
    meta: "Stripe · Google Drive",
    started_at: "2026-07-18T09:15:00Z",
  };

  it("carries exactly the run-row fields (incl. conversation_id, PRD-04 Seam C)", () => {
    expect(Object.keys(row).sort()).toEqual(
      [
        "conversation_id",
        "meta",
        "run_id",
        "started_at",
        "status",
        "title",
      ].sort(),
    );
  });

  it("uses a status drawn from the status tuple", () => {
    expect(ACTIVITY_RUN_STATUSES).toContain(row.status);
  });

  it("carries conversation_id and run_id as distinct navigable identities", () => {
    // The row addresses a conversation (open target) AND names its run — the
    // two are different fields, not aliases (PRD-04 Seam C).
    expect(row.conversation_id).not.toBe(
      row.run_id as unknown as ConversationId,
    );
  });

  it("requires conversation_id — a literal omitting it is a type error", () => {
    // @ts-expect-error missing conversation_id
    const incomplete: ActivityRunRow = {
      run_id: "run_002" as RunId,
      title: "No conversation",
      status: "done",
      meta: "",
      started_at: "2026-07-18T09:15:00Z",
    };
    void incomplete;

    // A complete literal type-checks.
    const complete: ActivityRunRow = {
      run_id: "run_003" as RunId,
      conversation_id: "conv_003" as ConversationId,
      title: "Complete",
      status: "done",
      meta: "",
      started_at: "2026-07-18T09:15:00Z",
    };
    expect(complete.conversation_id).toBe("conv_003");
  });
});
