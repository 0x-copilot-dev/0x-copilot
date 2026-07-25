import { describe, expect, it } from "vitest";
import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import { projectSubagentActivities } from "./subagentActivityProjection";

let nextSequence = 0;

function event(
  event_type: RuntimeEventEnvelope["event_type"],
  overrides: Partial<RuntimeEventEnvelope> = {},
): RuntimeEventEnvelope {
  nextSequence += 1;
  return {
    event_id: `evt-${nextSequence}`,
    run_id: "run-1",
    conversation_id: "conversation-1",
    sequence_no: nextSequence,
    event_type,
    activity_kind: "tool",
    payload: {},
    created_at: "2026-07-25T10:00:00.000Z",
    ...overrides,
  };
}

describe("projectSubagentActivities", () => {
  it("collapses canonical subagent tool frames by parent_task_id and preserves the terminal result", () => {
    nextSequence = 0;
    const started = event("tool_call_started", {
      parent_task_id: "task-research",
      subagent_id: "researcher",
      payload: {
        call_id: "call-search",
        tool_name: "web_search",
        args: { query: "enterprise search market" },
      },
    });
    const result = event("tool_result", {
      parent_task_id: "task-research",
      subagent_id: "researcher",
      status: "completed",
      summary: "Found 3 primary sources",
      payload: {
        call_id: "call-search",
        tool_name: "web_search",
        status: "completed",
        output: { hits: 3 },
      },
    });

    const projection = projectSubagentActivities([started, result]);

    expect(projection.activitiesByTask.get("task-research")).toEqual([
      {
        id: "call-search",
        kind: "tool",
        title: "web_search",
        status: "completed",
        summary: "Found 3 primary sources",
        inputSummary: '{"query":"enterprise search market"}',
        result: "Found 3 primary sources",
        isError: false,
      },
    ]);
  });

  it("keeps task scopes isolated, includes final reasoning, and rejects main-agent work even when it has a parent task", () => {
    nextSequence = 0;
    const scoped = event("reasoning_summary", {
      parent_task_id: "task-research",
      subagent_id: "researcher",
      activity_kind: "reasoning",
      payload: { summary: "Compare primary-source claims." },
    });
    const otherTask = event("tool_call_started", {
      parent_task_id: "task-press",
      subagent_id: "press-scout",
      payload: { call_id: "call-press", tool_name: "web_search" },
    });
    const mainAgent = event("tool_call_started", {
      source: "main_agent",
      parent_task_id: "task-research",
      payload: { call_id: "must-not-leak", tool_name: "web_search" },
    });

    const projection = projectSubagentActivities([
      scoped,
      otherTask,
      mainAgent,
      scoped,
    ]);

    expect(projection.activitiesByTask.get("task-research")).toEqual([
      expect.objectContaining({
        id: scoped.event_id,
        kind: "reasoning",
        title: "Reasoning",
        status: "completed",
        summary: "Compare primary-source claims.",
      }),
    ]);
    expect(projection.activitiesByTask.get("task-press")).toEqual([
      expect.objectContaining({ id: "call-press", kind: "tool" }),
    ]);
    expect(
      projection.activitiesByTask
        .get("task-research")
        ?.some((activity) => activity.id === "must-not-leak"),
    ).toBe(false);
    expect([...projection.activitiesByTask.values()].flat()).toHaveLength(2);
  });

  it("keeps a streamed reasoning row live until its final summary resolves it", () => {
    nextSequence = 0;
    const firstDelta = event("reasoning_summary_delta", {
      parent_task_id: "task-research",
      subagent_id: "researcher",
      payload: { summary: "Checking " },
    });
    const secondDelta = event("reasoning_summary_delta", {
      parent_task_id: "task-research",
      subagent_id: "researcher",
      payload: { summary: "sources" },
    });
    const final = event("reasoning_summary", {
      parent_task_id: "task-research",
      subagent_id: "researcher",
      payload: { summary: "Checked authoritative sources." },
    });

    const projection = projectSubagentActivities([
      firstDelta,
      secondDelta,
      final,
    ]);

    expect(projection.activitiesByTask.get("task-research")).toEqual([
      expect.objectContaining({
        id: firstDelta.event_id,
        kind: "reasoning",
        status: "completed",
        summary: "Checked authoritative sources.",
      }),
    ]);
  });
});
