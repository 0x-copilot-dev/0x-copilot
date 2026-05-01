import type { RuntimeEventEnvelope } from "@enterprise-search/api-types";
import type { ThreadMessageLike } from "@assistant-ui/react";
import { describe, expect, it } from "vitest";
import {
  applyRuntimeEvent,
  chatItemsToThreadMessages,
  resolveApprovalDecision,
  type ChatItem,
} from "./chatModel";

type ThreadMessageContent = Exclude<ThreadMessageLike["content"], string>;
type ThreadMessageContentPart = ThreadMessageContent[number];
type ThreadToolCallPart = Extract<
  ThreadMessageContentPart,
  { type: "tool-call" }
>;

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

function assistantMessage(
  items: ChatItem[],
): Extract<ChatItem, { kind: "message" }> {
  const item = items.find((candidate) => candidate.id === "assistant-run_123");
  if (!item || item.kind !== "message") {
    throw new Error("Expected assistant message");
  }
  return item;
}

function textPart(items: ChatItem[]): string | undefined {
  const part = assistantMessage(items).content.find(
    (candidate) => candidate.type === "text",
  );
  return part?.type === "text" ? part.text : undefined;
}

function toolPart(
  items: ChatItem[],
  toolName: string,
): ThreadToolCallPart | undefined {
  const part = assistantMessage(items).content.find(
    (candidate): candidate is ThreadToolCallPart =>
      candidate.type === "tool-call" && candidate.toolName === toolName,
  );
  return part;
}

describe("applyRuntimeEvent", () => {
  it("streams and reconciles assistant text on the run message", () => {
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

    expect(textPart(items)).toBe("Hello world");
    expect(chatItemsToThreadMessages(items, "run_123")[0].status).toEqual({
      type: "running",
    });

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

    expect(textPart(items)).toBe("Final answer.");
    expect(chatItemsToThreadMessages(items, null)[0].status).toEqual({
      type: "complete",
      reason: "stop",
    });
  });

  it("emits ordered progress, reasoning, subagent, and tool parts", () => {
    let items: ChatItem[] = [];

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

    expect(
      assistantMessage(items).content.map((part) =>
        part.type === "tool-call" ? part.toolName : part.type,
      ),
    ).toEqual(["run_progress", "reasoning", "run_subagent", "doc_search"]);
    expect(
      assistantMessage(items).content.some(
        (part) => part.type === "tool-call" && part.toolName === "write_todos",
      ),
    ).toBe(false);
  });

  it("updates subagent parts without creating empty namespace progress", () => {
    let items: ChatItem[] = [];

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
        summary: "Done.",
        payload: {
          task_id: "call_1",
          subagent_name: "general-purpose",
          status: "completed",
          summary: "Done.",
        },
      }),
    );

    const subagent = toolPart(items, "run_subagent");
    expect(subagent?.toolCallId).toBe("call_1");
    expect(subagent?.args).toMatchObject({
      subagent_name: "general-purpose",
      status: "completed",
      summary: "Done.",
    });
    expect(subagent?.result).toBe("Done.");
    expect(
      assistantMessage(items).content.some(
        (part) =>
          part.type === "tool-call" && part.toolCallId === "namespace_1",
      ),
    ).toBe(false);
  });

  it("keeps approval and MCP auth as action tool parts", () => {
    let items: ChatItem[] = [];

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "approval_1",
        event_type: "approval_requested",
        activity_kind: "approval",
        payload: {
          approval_id: "approval_123",
          message: "Approve the tool call?",
        },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "mcp_1",
        event_type: "mcp_auth_required",
        activity_kind: "mcp_auth",
        payload: {
          server_id: "server_123",
          server_name: "github",
          display_name: "GitHub",
          auth_url: "https://example.test/auth",
          expires_at: "2026-04-30T01:00:00Z",
          message: "Connect GitHub",
        },
      }),
    );

    expect(toolPart(items, "approval_request")?.toolCallId).toBe(
      "approval_123",
    );
    expect(toolPart(items, "mcp_auth_required")?.toolCallId).toBe("server_123");
    expect(chatItemsToThreadMessages(items, null)[0].status).toEqual({
      type: "requires-action",
      reason: "interrupt",
    });

    items = resolveApprovalDecision(items, "approval_123", "approved");

    expect(toolPart(items, "approval_request")?.result).toEqual({
      approval_id: "approval_123",
      decision: "approved",
    });
  });

  it("ignores heartbeat, internal, and unsupported message envelopes", () => {
    const items: ChatItem[] = [
      {
        id: "existing",
        kind: "message",
        role: "user",
        content: [{ type: "text", text: "Hello" }],
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
          event_id: "internal_1",
          event_type: "progress",
          activity_kind: "event",
          visibility: "internal",
          payload: { message: "scratchpad" },
        }),
      ),
    ).toBe(items);
    expect(
      applyRuntimeEvent(
        items,
        event({
          event_id: "unknown_1",
          event_type: "observation",
          activity_kind: "message",
          payload: { count: 1 },
        }),
      ),
    ).toBe(items);
  });
});
