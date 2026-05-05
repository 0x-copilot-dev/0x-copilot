import { describe, expect, it } from "vitest";
import type {
  RuntimeEventEnvelope,
  SubagentEntry,
} from "@enterprise-search/api-types";

import {
  applySubagentEvent,
  emptySubagentMap,
  isRunningStatus,
  seedSubagentMap,
  subagentsByRecency,
} from "./subagentReducer";

const CONVERSATION_ID = "conv_launch";
const RUN_ID = "run_alpha";

function event(
  overrides: Partial<RuntimeEventEnvelope> &
    Pick<RuntimeEventEnvelope, "event_type" | "task_id">,
): RuntimeEventEnvelope {
  return {
    event_id: `evt_${overrides.task_id}_${overrides.event_type}`,
    run_id: RUN_ID,
    conversation_id: CONVERSATION_ID,
    sequence_no: 1,
    activity_kind: "subagent",
    created_at: "2026-05-04T12:00:00Z",
    source: "subagent",
    payload: {},
    metadata: {},
    visibility: "user",
    redaction_state: "redacted",
    trace_id: "trace_1",
    ...overrides,
  } as RuntimeEventEnvelope;
}

function entry(overrides: Partial<SubagentEntry> = {}): SubagentEntry {
  return {
    task_id: "task_1",
    parent_run_id: RUN_ID,
    subagent_name: "research",
    status: "running",
    display_title: "Reviewing positioning",
    objective_summary: "Review competitive positioning",
    started_at: "2026-05-04T12:00:00Z",
    completed_at: null,
    duration_ms: null,
    result_summary: null,
    safe_error_code: null,
    safe_error_message: null,
    token_usage: null,
    ...overrides,
  };
}

describe("applySubagentEvent", () => {
  it("creates a snapshot on subagent_started", () => {
    const next = applySubagentEvent(
      emptySubagentMap(),
      event({
        event_type: "subagent_started",
        task_id: "task_1",
        subagent_id: "research",
        summary: "Investigate competitive frame",
        display_title: "Competitive frame",
        created_at: "2026-05-04T12:00:00Z",
      }),
    );
    const snapshot = next.get("task_1");
    expect(snapshot?.status).toBe("running");
    expect(snapshot?.subagent_name).toBe("research");
    expect(snapshot?.objective_summary).toBe("Investigate competitive frame");
    expect(snapshot?.started_at).toBe("2026-05-04T12:00:00Z");
  });

  it("transitions to completed and records duration_ms", () => {
    const started = applySubagentEvent(
      emptySubagentMap(),
      event({
        event_type: "subagent_started",
        task_id: "task_1",
        subagent_id: "research",
        created_at: "2026-05-04T12:00:00Z",
      }),
    );
    const completed = applySubagentEvent(
      started,
      event({
        event_type: "subagent_completed",
        task_id: "task_1",
        summary: "Glean leads on legacy; we lead on agentic.",
        created_at: "2026-05-04T12:00:12Z",
      }),
    );
    const snapshot = completed.get("task_1");
    expect(snapshot?.status).toBe("completed");
    expect(snapshot?.completed_at).toBe("2026-05-04T12:00:12Z");
    expect(snapshot?.duration_ms).toBe(12_000);
    expect(snapshot?.result_summary).toBe(
      "Glean leads on legacy; we lead on agentic.",
    );
  });

  it("returns the same map identity when nothing changes", () => {
    const seeded = seedSubagentMap([entry({ task_id: "task_1" })]);
    const same = applySubagentEvent(
      seeded,
      event({
        event_type: "subagent_progress",
        task_id: "task_1",
        display_title: "Reviewing positioning",
      }),
    );
    expect(same).toBe(seeded);
  });

  it("propagates cancelled and failed statuses", () => {
    const cancelled = applySubagentEvent(
      seedSubagentMap([entry({ task_id: "task_c" })]),
      event({
        event_type: "subagent_completed",
        task_id: "task_c",
        status: "cancelled",
        created_at: "2026-05-04T12:00:05Z",
      }),
    );
    expect(cancelled.get("task_c")?.status).toBe("cancelled");

    const failed = applySubagentEvent(
      seedSubagentMap([entry({ task_id: "task_f" })]),
      event({
        event_type: "subagent_completed",
        task_id: "task_f",
        status: "failed",
        created_at: "2026-05-04T12:00:05Z",
      }),
    );
    expect(failed.get("task_f")?.status).toBe("failed");
  });

  it("ignores events from a non-subagent source", () => {
    const seeded = seedSubagentMap([entry()]);
    const same = applySubagentEvent(
      seeded,
      event({
        event_type: "subagent_started",
        task_id: "task_1",
        source: "tool",
      }),
    );
    expect(same).toBe(seeded);
  });
});

describe("subagentsByRecency", () => {
  it("orders by completed_at desc then started_at desc", () => {
    const map = seedSubagentMap([
      entry({
        task_id: "early",
        started_at: "2026-05-04T11:00:00Z",
        completed_at: "2026-05-04T11:05:00Z",
      }),
      entry({
        task_id: "late",
        started_at: "2026-05-04T11:10:00Z",
        completed_at: "2026-05-04T11:20:00Z",
      }),
      entry({
        task_id: "running",
        started_at: "2026-05-04T11:30:00Z",
        completed_at: null,
      }),
    ]);
    const order = subagentsByRecency(map).map((s) => s.task_id);
    expect(order).toEqual(["running", "late", "early"]);
  });
});

describe("isRunningStatus", () => {
  it("matches queued and running", () => {
    expect(isRunningStatus("queued")).toBe(true);
    expect(isRunningStatus("running")).toBe(true);
    expect(isRunningStatus("completed")).toBe(false);
    expect(isRunningStatus("cancelled")).toBe(false);
  });
});
