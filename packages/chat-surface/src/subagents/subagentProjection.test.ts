// PR-3.8 — projectSubagents unit tests (FR-3.17 / FR-3.3).
//
// The selector reduces the single canonical run event stream into (1) the
// subagent snapshot map that drives the Agents-tab "N live" count and (2) the
// dispatched fleets that drive the inline `SubagentFleetCard`. These assert the
// grouping, head counts, and lifecycle-status parity with the host reducer.

import { describe, expect, it } from "vitest";

import type {
  RuntimeApiEventType,
  RuntimeEventEnvelope,
} from "@0x-copilot/api-types";

import { projectSubagents } from "./subagentProjection";

let nextSeq = 0;

function evt(
  type: RuntimeApiEventType,
  overrides: Partial<RuntimeEventEnvelope> = {},
): RuntimeEventEnvelope {
  const seq = nextSeq;
  nextSeq += 1;
  return {
    event_id: overrides.event_id ?? `evt-${seq}`,
    run_id: "run-1",
    conversation_id: "conv-1",
    sequence_no: overrides.sequence_no ?? seq,
    event_type: type,
    activity_kind: "subagent",
    payload: {},
    created_at: new Date(1700000000000 + seq * 1000).toISOString(),
    ...overrides,
  };
}

function child(
  type: RuntimeApiEventType,
  taskId: string,
  overrides: Partial<RuntimeEventEnvelope> = {},
): RuntimeEventEnvelope {
  return evt(type, {
    source: "subagent",
    task_id: taskId,
    ...overrides,
  });
}

describe("projectSubagents", () => {
  it("returns empty state for zero events", () => {
    nextSeq = 0;
    const out = projectSubagents([]);
    expect(out.subagents.size).toBe(0);
    expect(out.fleets).toEqual([]);
  });

  it("groups children under their fleet and derives live head counts", () => {
    nextSeq = 0;
    const out = projectSubagents([
      evt("subagent_fleet_started", {
        source: "main_agent",
        payload: {
          fleet_id: "fleet-1",
          title: "Parallel research",
          agent_ids: ["doc_reader", "press_scout"],
        },
      }),
      child("subagent_started", "task_alpha", {
        subagent_id: "doc_reader",
        payload: { parent_fleet_id: "fleet-1" },
      }),
      child("subagent_started", "task_beta", {
        subagent_id: "press_scout",
        payload: { parent_fleet_id: "fleet-1" },
      }),
    ]);

    expect(out.fleets).toHaveLength(1);
    const fleet = out.fleets[0];
    expect(fleet.fleetId).toBe("fleet-1");
    expect(fleet.total).toBe(2);
    expect(fleet.running).toBe(2);
    expect(fleet.done).toBe(0);
    expect(fleet.children.map((c) => c.task_id)).toEqual([
      "task_alpha",
      "task_beta",
    ]);
    // Every child also lands in the flat snapshot map (feeds the Agents count).
    expect(out.subagents.size).toBe(2);
  });

  it("flips a child running → done on completion without dropping siblings", () => {
    nextSeq = 0;
    const out = projectSubagents([
      evt("subagent_fleet_started", {
        source: "main_agent",
        payload: { fleet_id: "f", agent_ids: ["a", "b"] },
      }),
      child("subagent_started", "t1", { payload: { parent_fleet_id: "f" } }),
      child("subagent_started", "t2", { payload: { parent_fleet_id: "f" } }),
      child("subagent_completed", "t1", {
        status: "completed",
        payload: { parent_fleet_id: "f" },
      }),
    ]);

    const fleet = out.fleets[0];
    expect(fleet.running).toBe(1);
    expect(fleet.done).toBe(1);
    expect(fleet.total).toBe(2);
    expect(out.subagents.get("t1")?.status).toBe("completed");
    expect(out.subagents.get("t2")?.status).toBe("running");
  });

  it("records fleet elapsed + finished on subagent_fleet_finished", () => {
    nextSeq = 0;
    const out = projectSubagents([
      evt("subagent_fleet_started", {
        source: "main_agent",
        payload: { fleet_id: "f", agent_ids: ["a"] },
      }),
      child("subagent_started", "t1", { payload: { parent_fleet_id: "f" } }),
      child("subagent_completed", "t1", { status: "completed" }),
      evt("subagent_fleet_finished", {
        source: "main_agent",
        payload: { fleet_id: "f", elapsed: "12s" },
      }),
    ]);

    const fleet = out.fleets[0];
    expect(fleet.finished).toBe(true);
    expect(fleet.elapsed).toBe("12s");
    expect(fleet.running).toBe(0);
    expect(fleet.done).toBe(1);
  });

  it("projects a standalone (non-fleet) subagent into the map but no fleet", () => {
    nextSeq = 0;
    const out = projectSubagents([
      child("subagent_started", "solo", { subagent_id: "researcher" }),
    ]);
    expect(out.fleets).toEqual([]);
    expect(out.subagents.get("solo")?.status).toBe("running");
  });

  it("marks a paused child not-running and clears it on resume", () => {
    nextSeq = 0;
    const paused = projectSubagents([
      child("subagent_started", "t1"),
      child("subagent_paused", "t1", {
        payload: { reason: "approval", source_event_id: "gate-1" },
      }),
    ]);
    expect(paused.subagents.get("t1")?.status).toBe("paused");

    const resumed = projectSubagents([
      child("subagent_started", "t1"),
      child("subagent_paused", "t1", { payload: { reason: "approval" } }),
      child("subagent_resumed", "t1", { payload: {} }),
    ]);
    expect(resumed.subagents.get("t1")?.status).toBe("running");
  });

  it("is idempotent on replay — duplicate event_ids do not double-count", () => {
    nextSeq = 0;
    const started = child("subagent_started", "t1", {
      event_id: "dup",
      payload: { parent_fleet_id: "f" },
    });
    const out = projectSubagents([
      evt("subagent_fleet_started", {
        source: "main_agent",
        payload: { fleet_id: "f", agent_ids: ["a"] },
      }),
      started,
      started,
    ]);
    expect(out.subagents.size).toBe(1);
    expect(out.fleets[0].total).toBe(1);
    expect(out.fleets[0].running).toBe(1);
  });
});
