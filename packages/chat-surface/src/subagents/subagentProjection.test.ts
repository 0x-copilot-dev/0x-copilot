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

import { isRunningStatus } from "../workspace/workspaceHelpers";
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

  // The client half of "Stop must stop". The worker closes every subagent a
  // cancelled run left open (`close_open_subagents_as_cancelled`), and the ONLY
  // thing that makes that terminal frame clear the cockpit is this reduction:
  // `cancelled` has to land as a terminal status, not fall back to `running`.
  // Assert through `isRunningStatus` — the predicate `RunWorkspaceRail` counts
  // with — rather than restating the status word, so a divergence between the
  // projection and the badge cannot pass.
  it("closes a cancelled child so the Agents 'N live' count clears", () => {
    nextSeq = 0;
    const out = projectSubagents([
      evt("subagent_fleet_started", {
        source: "main_agent",
        payload: { fleet_id: "f", agent_ids: ["a"] },
      }),
      child("subagent_started", "t1", { payload: { parent_fleet_id: "f" } }),
      child("subagent_completed", "t1", {
        status: "cancelled",
        payload: {
          parent_fleet_id: "f",
          status: "cancelled",
          summary: "Stopped when the run was cancelled.",
        },
      }),
      evt("subagent_fleet_finished", {
        source: "main_agent",
        payload: { fleet_id: "f" },
      }),
    ]);

    expect(out.subagents.get("t1")?.status).toBe("cancelled");
    const live = [...out.subagents.values()].filter((entry) =>
      isRunningStatus(entry.status),
    );
    expect(live).toHaveLength(0);
    expect(out.fleets[0].running).toBe(0);
    expect(out.fleets[0].done).toBe(1);
    // A cancelled child is not a success — the fleet chrome must not claim one.
    expect(out.fleets[0].failed).toBe(1);
  });

  it("closes a child on a status-less subagent_completed, labelling it completed", () => {
    // The degraded case, and the reduction only holds one way. The projection
    // reads the ENVELOPE's `status` and never `payload.status`, so a frame that
    // reaches the client without the projected field falls back to `completed`:
    // the count still clears — that is the property that matters, and the whole
    // reason a missing status cannot strand a spinning card — but the label is
    // then wrong for a cancelled child. Pinned rather than glossed, so anyone
    // who assumes the payload is a second source of truth is corrected here.
    nextSeq = 0;
    const out = projectSubagents([
      child("subagent_started", "t1"),
      child("subagent_completed", "t1", { payload: { status: "cancelled" } }),
    ]);

    const entry = out.subagents.get("t1");
    expect(entry).toBeDefined();
    expect(isRunningStatus(entry!.status)).toBe(false);
    expect(entry!.status).toBe("completed");
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

  it("retains a failed terminal child so the fleet chrome cannot report success", () => {
    nextSeq = 0;
    const out = projectSubagents([
      evt("subagent_fleet_started", {
        source: "main_agent",
        payload: { fleet_id: "f", agent_ids: ["a"] },
      }),
      child("subagent_started", "t1", { payload: { parent_fleet_id: "f" } }),
      child("subagent_completed", "t1", {
        status: "failed",
        payload: { parent_fleet_id: "f", error_message: "Timed out" },
      }),
      evt("subagent_fleet_finished", {
        source: "main_agent",
        payload: { fleet_id: "f" },
      }),
    ]);

    expect(out.fleets[0]).toMatchObject({
      running: 0,
      done: 1,
      failed: 1,
    });
  });

  it("projects a standalone (non-fleet) subagent into the map but no fleet", () => {
    nextSeq = 0;
    const out = projectSubagents([
      child("subagent_started", "solo", { subagent_id: "researcher" }),
    ]);
    expect(out.fleets).toEqual([]);
    expect(out.subagents.get("solo")?.status).toBe("running");
  });

  it("retains safe Focus presentation fields and progress summary across lifecycle frames", () => {
    nextSeq = 0;
    const out = projectSubagents([
      child("subagent_started", "task_research", {
        subagent_id: "researcher",
        payload: {
          parent_task_id: "main-agent",
          parent_agent_role: "orchestrator",
          parent_agent_name: "Orchestrator",
          model_display_label: "Haiku 4.5",
        },
      }),
      child("subagent_progress", "task_research", {
        summary: "Searching primary documentation",
        payload: {},
      }),
      child("subagent_progress", "task_research", {
        payload: { current_activity: "Comparing conflicting claims" },
      }),
    ]);

    expect(out.subagents.get("task_research")).toMatchObject({
      parent_task_id: "main-agent",
      parent_agent_role: "orchestrator",
      parent_agent_name: "Orchestrator",
      model_display_label: "Haiku 4.5",
      current_activity: "Comparing conflicting claims",
    });
  });

  it("projects a lone subagent as a fleet-of-one, running → done with a result", () => {
    nextSeq = 0;
    const out = projectSubagents([
      evt("subagent_fleet_started", {
        source: "main_agent",
        payload: { fleet_id: "solo-fleet", agent_ids: ["researcher"] },
      }),
      child("subagent_started", "t_solo", {
        subagent_id: "researcher",
        summary: "Investigate the launch",
        payload: { parent_fleet_id: "solo-fleet" },
      }),
      child("subagent_completed", "t_solo", {
        status: "completed",
        summary: "Launch looks on track",
        payload: { parent_fleet_id: "solo-fleet" },
      }),
      evt("subagent_fleet_finished", {
        source: "main_agent",
        payload: { fleet_id: "solo-fleet", elapsed: "8s" },
      }),
    ]);

    expect(out.fleets).toHaveLength(1);
    const fleet = out.fleets[0];
    expect(fleet.fleetId).toBe("solo-fleet");
    expect(fleet.total).toBe(1);
    expect(fleet.running).toBe(0);
    expect(fleet.done).toBe(1);
    expect(fleet.finished).toBe(true);
    expect(fleet.elapsed).toBe("8s");
    expect(fleet.children).toHaveLength(1);
    const child0 = fleet.children[0];
    expect(child0.task_id).toBe("t_solo");
    expect(child0.status).toBe("completed");
    expect(child0.result_summary).toBe("Launch looks on track");
  });

  it("back-stamps earlier child lifecycle frames from the fleet's declared task ids", () => {
    nextSeq = 0;
    // This is the desktop file-store ordering: the task lifecycle frame comes
    // from the messages stream, then the grouped dispatch bookend comes from
    // the updates stream. The lifecycle payload has no parent_fleet_id.
    const out = projectSubagents([
      child("subagent_started", "call_prime", {
        subagent_id: "general-purpose",
        summary: "Check whether 97 is prime.",
      }),
      evt("subagent_fleet_started", {
        source: "subagent",
        payload: {
          fleet_id: "fleet-prime",
          agent_ids: ["general-purpose"],
          task_ids: ["call_prime"],
        },
      }),
      child("subagent_completed", "call_prime", {
        status: "completed",
        summary: "97 is prime.",
      }),
      evt("subagent_fleet_finished", {
        source: "subagent",
        payload: { fleet_id: "fleet-prime", elapsed: "0:01" },
      }),
    ]);

    const fleet = out.fleets[0];
    expect(fleet.children).toHaveLength(1);
    expect(fleet.children[0]).toMatchObject({
      task_id: "call_prime",
      status: "completed",
      result_summary: "97 is prime.",
    });
    expect(fleet.done).toBe(1);
    expect(fleet.total).toBe(1);
  });

  it("keeps declared parallel capacity while lifecycle frames are still arriving", () => {
    nextSeq = 0;
    const out = projectSubagents([
      evt("subagent_fleet_started", {
        source: "subagent",
        payload: {
          fleet_id: "fleet-partial",
          agent_ids: ["general-purpose", "general-purpose"],
          task_ids: ["call_one", "call_two"],
        },
      }),
      child("subagent_started", "call_one", {
        subagent_id: "general-purpose",
        payload: { parent_fleet_id: "fleet-partial" },
      }),
    ]);

    expect(out.fleets[0]).toMatchObject({
      total: 2,
      running: 2,
      done: 0,
    });
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
