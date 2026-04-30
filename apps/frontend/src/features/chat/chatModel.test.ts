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

    const activityItem = items.find((item) => item.kind === "run-activity");
    expect(activityItem?.kind).toBe("run-activity");
    if (activityItem?.kind !== "run-activity") {
      throw new Error("Expected run activity item");
    }
    expect(activityItem.activity.reasoning[0].text).toBe("Checking sources");
    expect(activityItem.activity.subagents[0].name).toBe("researcher");
    expect(activityItem.activity.subagents[0].tools[0].name).toBe("doc_search");
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
