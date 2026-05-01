import type { RuntimeEventEnvelope } from "@enterprise-search/api-types";
import { describe, expect, it } from "vitest";
import { applyRuntimeEvent, type ChatItem } from "./chatModel";

function event(overrides: Partial<RuntimeEventEnvelope>): RuntimeEventEnvelope {
  return {
    event_id: "event_123",
    run_id: "run_123",
    conversation_id: "conversation_123",
    sequence_no: 1,
    event_type: "progress",
    activity_kind: "event",
    status: "running",
    payload: {},
    created_at: "2026-04-30T00:00:00Z",
    ...overrides,
  };
}

function messageText(items: ChatItem[], id: string): string | undefined {
  const item = items.find((candidate) => candidate.id === id);
  return item?.kind === "message" ? item.text : undefined;
}

describe("applyRuntimeEvent", () => {
  it("concatenates model deltas and reconciles the final assistant response", () => {
    let items: ChatItem[] = [];

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "event_1",
        event_type: "model_delta",
        activity_kind: "message",
        payload: { delta: "Hello" },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "event_2",
        event_type: "model_delta",
        activity_kind: "message",
        payload: { delta: " world" },
      }),
    );

    expect(messageText(items, "assistant-run_123")).toBe("Hello world");

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "event_3",
        event_type: "final_response",
        activity_kind: "message",
        status: "completed",
        payload: { message: "Final answer." },
      }),
    );

    expect(messageText(items, "assistant-run_123")).toBe("Final answer.");
  });

  it("keeps completed run activity in the live thread", () => {
    let items: ChatItem[] = [];

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "run_started",
        event_type: "run_started",
        activity_kind: "run",
        status: "running",
        payload: { status: "running" },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "final_response",
        event_type: "final_response",
        activity_kind: "message",
        status: "completed",
        payload: { message: "Hello!" },
      }),
    );

    expect(items.some((item) => item.kind === "run-activity")).toBe(true);

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "run_completed",
        event_type: "run_completed",
        activity_kind: "run",
        status: "completed",
        payload: { status: "completed" },
      }),
    );

    expect(messageText(items, "assistant-run_123")).toBe("Hello!");
    const activity = items.find((item) => item.kind === "run-activity");
    expect(activity?.kind === "run-activity" && activity.activity.status).toBe(
      "completed",
    );
  });

  it("projects tool, subagent, and reasoning events into run activity", () => {
    let items: ChatItem[] = [];

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "reasoning_1",
        event_type: "reasoning_summary_delta",
        activity_kind: "reasoning",
        payload: { delta: "Checking sources" },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "subagent_1",
        event_type: "subagent_started",
        activity_kind: "subagent",
        task_id: "task_123",
        subagent_id: "researcher",
        payload: {
          task_id: "task_123",
          subagent_name: "researcher",
          status: "started",
        },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "tool_1",
        event_type: "tool_call_started",
        activity_kind: "tool",
        parent_task_id: "task_123",
        span_id: "call_123",
        payload: {
          tool_name: "doc_search",
          call_id: "call_123",
          status: "running",
        },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "todo_tool_1",
        event_type: "tool_call_delta",
        activity_kind: "tool",
        visibility: "internal",
        parent_task_id: "task_123",
        span_id: "todo_call_123",
        payload: {
          tool_name: "write_todos",
          call_id: "todo_call_123",
          delta: '{"todos":[{"content":"internal scratchpad"}]}',
          status: "running",
        },
      }),
    );

    const activityItem = items.find((item) => item.kind === "run-activity");
    expect(activityItem?.kind).toBe("run-activity");
    if (activityItem?.kind !== "run-activity") {
      throw new Error("Expected run activity item");
    }
    expect(activityItem.activity.reasoning[0].text).toBe("Checking sources");
    expect(activityItem.activity.subagents[0].name).toBe("researcher");
    expect(activityItem.activity.subagents[0].tools[0].name).toBe("doc_search");
    expect(activityItem.activity.subagents[0].tools).toHaveLength(1);
  });

  it("keeps subagent progress from overwriting the parent run overview", () => {
    let items: ChatItem[] = [];

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "run_queued",
        event_type: "run_queued",
        activity_kind: "run",
        status: "queued",
        display_title: "Run queued",
        summary: "Run was queued for runtime execution.",
        payload: { status: "queued" },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "run_started",
        event_type: "run_started",
        activity_kind: "run",
        status: "running",
        display_title: "Run started",
        summary: "Run started",
        payload: { status: "running" },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "subagent_started_1",
        event_type: "subagent_started",
        activity_kind: "subagent",
        task_id: "call_1",
        subagent_id: "general-purpose",
        status: "queued",
        summary: "Write a merge-sorted-lists function.",
        payload: {
          task_id: "call_1",
          subagent_name: "general-purpose",
          status: "queued",
          summary: "Write a merge-sorted-lists function.",
        },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "namespace_progress_1",
        event_type: "subagent_progress",
        activity_kind: "subagent",
        task_id: "namespace_1",
        status: "running",
        display_title: "Subagent update",
        payload: {
          task_id: "namespace_1",
          status: "running",
        },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "todo_progress_1",
        event_type: "subagent_progress",
        activity_kind: "subagent",
        visibility: "internal",
        parent_task_id: "call_1",
        task_id: "call_1",
        status: "running",
        display_title: "Subagent update",
        summary: "Updated todo list to [{'content': 'internal scratchpad'}]",
        payload: {
          task_id: "call_1",
          status: "running",
          message: "Updated todo list to [{'content': 'internal scratchpad'}]",
        },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "subagent_completed_1",
        event_type: "subagent_completed",
        activity_kind: "subagent",
        task_id: "call_1",
        subagent_id: "general-purpose",
        status: "completed",
        summary: "```python\nprint('large result')\n```",
        payload: {
          task_id: "call_1",
          subagent_name: "general-purpose",
          status: "completed",
          summary: "```python\nprint('large result')\n```",
        },
      }),
    );

    const activityItem = items.find((item) => item.kind === "run-activity");
    expect(activityItem?.kind).toBe("run-activity");
    if (activityItem?.kind !== "run-activity") {
      throw new Error("Expected run activity item");
    }
    expect(activityItem.activity.title).toBe("Run started");
    expect(activityItem.activity.status).toBe("running");
    expect(activityItem.activity.summary).toBe("Run started");
    expect(activityItem.activity.subagents).toHaveLength(1);
    expect(activityItem.activity.subagents[0].status).toBe("completed");
    expect(
      activityItem.activity.events.some(
        (activityEvent) => activityEvent.title === "Subagent update",
      ),
    ).toBe(false);
  });

  it("ignores heartbeat events and unsupported non-text envelopes", () => {
    const items: ChatItem[] = [
      {
        id: "existing",
        kind: "message",
        role: "user",
        text: "Hello",
      },
    ];

    expect(
      applyRuntimeEvent(
        items,
        event({
          event_id: "heartbeat_1",
          event_type: "heartbeat",
          activity_kind: "heartbeat",
        }),
      ),
    ).toBe(items);

    expect(
      applyRuntimeEvent(
        items,
        event({
          event_id: "unknown_1",
          event_type: "progress",
          activity_kind: "message",
          payload: { count: 1 },
        }),
      ),
    ).toBe(items);
  });
});
