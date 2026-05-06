// PR 3.2.1 — selector-hook coverage.
//
// AC-5 (archive parity with the reducer) is covered by piping live
// events through `applyRuntimeEvent` and asserting the selector returns
// what the in-thread renderer also reads. AC-7 (referential stability)
// covered by `renderHook` + a no-op state update.

import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import type { RuntimeEventEnvelope } from "@enterprise-search/api-types";
import { applyRuntimeEvent, type ChatItem } from "../../chatModel";
import {
  collectSubagentActivities,
  useSubagentActivities,
} from "./useSubagentActivities";

function event(overrides: Partial<RuntimeEventEnvelope>): RuntimeEventEnvelope {
  return {
    event_id: "event_id",
    run_id: "run_123",
    conversation_id: "conversation_123",
    sequence_no: 1,
    event_type: "progress",
    activity_kind: "event",
    status: "running",
    payload: {},
    created_at: "2026-05-06T00:00:00Z",
    ...overrides,
  };
}

function pipeEvents(events: RuntimeEventEnvelope[]): ChatItem[] {
  let items: ChatItem[] = [];
  for (const e of events) {
    items = applyRuntimeEvent(items, e);
  }
  return items;
}

describe("collectSubagentActivities", () => {
  it("returns the empty map for an empty thread", () => {
    expect(collectSubagentActivities([]).size).toBe(0);
  });

  it("returns the empty map when there are no run_subagent tool parts", () => {
    const items = pipeEvents([
      event({
        event_id: "tool_solo",
        event_type: "tool_call_completed",
        activity_kind: "tool",
        span_id: "call_solo",
        payload: {
          tool_name: "search_corpus",
          call_id: "call_solo",
          status: "completed",
          summary: "ok",
        },
      }),
    ]);
    expect(collectSubagentActivities(items).size).toBe(0);
  });

  it("projects nested tool calls into Map<task_id, activities[]>", () => {
    // Emit a realistic event sequence: subagent_started, then a started
    // + completed pair per inner tool call (matches what the worker
    // emits in production and what `runtime_events` persists).
    const items = pipeEvents([
      event({
        event_id: "subagent_1",
        event_type: "subagent_started",
        activity_kind: "subagent",
        task_id: "task_doc_reader",
        subagent_id: "doc_reader",
        payload: {
          task_id: "task_doc_reader",
          subagent_name: "doc_reader",
          status: "started",
        },
      }),
      event({
        event_id: "tool_a_started",
        event_type: "tool_call_started",
        activity_kind: "tool",
        parent_task_id: "task_doc_reader",
        span_id: "call_a",
        payload: {
          tool_name: "search_notion",
          call_id: "call_a",
          status: "running",
        },
      }),
      event({
        event_id: "tool_a_completed",
        event_type: "tool_call_completed",
        activity_kind: "tool",
        parent_task_id: "task_doc_reader",
        span_id: "call_a",
        status: "completed",
        payload: {
          tool_name: "search_notion",
          call_id: "call_a",
          status: "completed",
          summary: "4 hits",
        },
      }),
      event({
        event_id: "tool_b_started",
        event_type: "tool_call_started",
        activity_kind: "tool",
        parent_task_id: "task_doc_reader",
        span_id: "call_b",
        payload: {
          tool_name: "read_file",
          call_id: "call_b",
          status: "running",
        },
      }),
      event({
        event_id: "tool_b_completed",
        event_type: "tool_call_completed",
        activity_kind: "tool",
        parent_task_id: "task_doc_reader",
        span_id: "call_b",
        status: "completed",
        payload: {
          tool_name: "read_file",
          call_id: "call_b",
          status: "completed",
          summary: "GTM/FY26-Q1 plan",
        },
      }),
    ]);

    const map = collectSubagentActivities(items);
    expect(map.size).toBe(1);
    const activities = map.get("task_doc_reader") ?? [];
    expect(activities).toHaveLength(2);
    expect(activities[0]).toMatchObject({
      id: "call_a",
      kind: "tool",
      title: "search_notion",
    });
    expect(activities[0]?.status).toBe("completed");
    expect(activities[1]).toMatchObject({
      id: "call_b",
      title: "read_file",
    });
    expect(activities[1]?.status).toBe("completed");
  });

  it("registers task_ids with no inner activities (preserves the empty fallback)", () => {
    const items = pipeEvents([
      event({
        event_id: "subagent_solo",
        event_type: "subagent_started",
        activity_kind: "subagent",
        task_id: "task_silent",
        subagent_id: "silent",
        payload: {
          task_id: "task_silent",
          subagent_name: "silent",
          status: "started",
        },
      }),
    ]);
    const map = collectSubagentActivities(items);
    expect(map.has("task_silent")).toBe(true);
    expect(map.get("task_silent")).toEqual([]);
  });

  it("keeps entries for separate task_ids isolated", () => {
    const items = pipeEvents([
      event({
        event_id: "sa_a",
        event_type: "subagent_started",
        activity_kind: "subagent",
        task_id: "task_a",
        payload: { task_id: "task_a", subagent_name: "a", status: "started" },
      }),
      event({
        event_id: "sa_b",
        event_type: "subagent_started",
        activity_kind: "subagent",
        task_id: "task_b",
        payload: { task_id: "task_b", subagent_name: "b", status: "started" },
      }),
      event({
        event_id: "tool_a",
        event_type: "tool_call_completed",
        activity_kind: "tool",
        parent_task_id: "task_a",
        span_id: "call_a",
        payload: { tool_name: "x", call_id: "call_a", status: "completed" },
      }),
      event({
        event_id: "tool_b",
        event_type: "tool_call_completed",
        activity_kind: "tool",
        parent_task_id: "task_b",
        span_id: "call_b",
        payload: { tool_name: "y", call_id: "call_b", status: "completed" },
      }),
    ]);
    const map = collectSubagentActivities(items);
    expect(map.get("task_a")).toHaveLength(1);
    expect(map.get("task_b")).toHaveLength(1);
    expect(map.get("task_a")?.[0]?.title).toBe("x");
    expect(map.get("task_b")?.[0]?.title).toBe("y");
  });
});

describe("useSubagentActivities", () => {
  it("is reference-stable when items reference does not change", () => {
    const items: ChatItem[] = [];
    const { result, rerender } = renderHook(
      ({ value }: { value: ChatItem[] }) => useSubagentActivities(value),
      { initialProps: { value: items } },
    );
    const first = result.current;
    rerender({ value: items });
    expect(result.current).toBe(first);
  });

  it("recomputes when items reference changes", () => {
    const empty: ChatItem[] = [];
    const populated = pipeEvents([
      event({
        event_id: "sa1",
        event_type: "subagent_started",
        activity_kind: "subagent",
        task_id: "task_x",
        payload: { task_id: "task_x", subagent_name: "x", status: "started" },
      }),
    ]);
    const { result, rerender } = renderHook(
      ({ value }: { value: ChatItem[] }) => useSubagentActivities(value),
      { initialProps: { value: empty } },
    );
    expect(result.current.size).toBe(0);
    rerender({ value: populated });
    expect(result.current.has("task_x")).toBe(true);
  });
});
