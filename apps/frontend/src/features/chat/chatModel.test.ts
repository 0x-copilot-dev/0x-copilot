import type {
  Message,
  RuntimeEventEnvelope,
} from "@enterprise-search/api-types";
import type { ThreadMessageLike } from "@assistant-ui/react";
import { describe, expect, it } from "vitest";
import {
  applyRuntimeEvent,
  chatItemsToThreadMessages,
  messagesToChatItems,
  resolveApprovalDecision,
  resolveMcpAuthSkip,
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

function message(overrides: Partial<Message>): Message {
  return {
    message_id: "message_123",
    conversation_id: "conversation_123",
    org_id: "org_123",
    run_id: null,
    role: "assistant",
    content_text: "Stored text",
    content_format: "text/plain",
    parent_message_id: null,
    token_count: null,
    trace_id: null,
    status: "created",
    created_at: "2026-04-30T00:00:00Z",
    edited_at: null,
    deleted_at: null,
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

function firstThreadMessage(
  items: ChatItem[],
  activeRunId: string | null = null,
): ThreadMessageLike {
  const [message] = chatItemsToThreadMessages(items, activeRunId);
  if (!message) {
    throw new Error("Expected thread message");
  }
  return message;
}

const performanceMetrics = {
  started_at: "2026-04-30T00:00:00Z",
  completed_at: "2026-04-30T00:00:02Z",
  duration_ms: 2000,
  chunk_count: 4,
  first_chunk_at: "2026-04-30T00:00:00.250Z",
  first_chunk_ms: 250,
  usage: {
    input: 12,
    output: 8,
    total: 20,
    output_per_second: 4,
  },
};

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
    expect(firstThreadMessage(items, "run_123").status).toEqual({
      type: "running",
    });

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "event_3",
        event_type: "final_response",
        activity_kind: "message",
        status: "completed",
        payload: {
          message: "Final answer.",
          performance_metrics: performanceMetrics,
        },
      }),
    );

    expect(textPart(items)).toBe("Final answer.");
    expect(firstThreadMessage(items).status).toEqual({
      type: "complete",
      reason: "stop",
    });
    expect(
      assistantMessage(items).metadata?.custom?.performance_metrics,
    ).toEqual(performanceMetrics);
    expect(assistantMessage(items).metadata?.timing).toMatchObject({
      totalStreamTime: 2000,
      tokenCount: 8,
      tokensPerSecond: 4,
      totalChunks: 4,
      toolCallCount: 0,
    });
  });

  it("preserves fenced code formatting in final assistant text", () => {
    const code = [
      "```python",
      "def is_prime(n: int) -> bool:",
      "    if n <= 1:",
      "        return False",
      "    return True",
      "```",
    ].join("\n");

    const items = applyRuntimeEvent(
      [],
      event({
        event_id: "code_final_1",
        event_type: "final_response",
        activity_kind: "message",
        status: "completed",
        payload: { message: code },
      }),
    );

    expect(textPart(items)).toBe(code);
  });

  it("emits ordered reasoning, subagent, and tool parts", () => {
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
    expect(items).toEqual([]);

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
    ).toEqual(["reasoning", "run_subagent"]);
    expect(toolPart(items, "run_subagent")?.args).toMatchObject({
      activities: [
        {
          id: "call_123",
          kind: "tool",
          title: "doc_search",
          status: "running",
        },
      ],
    });
    expect(
      assistantMessage(items).content.some(
        (part) => part.type === "tool-call" && part.toolName === "write_todos",
      ),
    ).toBe(false);
  });

  it("hydrates assistant history from replayed runtime events", () => {
    const replayEvents = [
      event({
        event_id: "tool_1",
        sequence_no: 2,
        event_type: "tool_call_completed",
        activity_kind: "tool",
        span_id: "call_123",
        payload: {
          tool_name: "doc_search",
          call_id: "call_123",
          status: "completed",
          summary: "Found two docs.",
        },
      }),
      event({
        event_id: "reasoning_1",
        sequence_no: 1,
        event_type: "reasoning_summary",
        activity_kind: "reasoning",
        payload: { summary: "Checking sources." },
      }),
      event({
        event_id: "final_1",
        sequence_no: 3,
        event_type: "final_response",
        activity_kind: "message",
        status: "completed",
        payload: {
          message: "Final answer.",
          performance_metrics: performanceMetrics,
        },
      }),
    ];
    const liveItems = replayEvents
      .sort((left, right) => left.sequence_no - right.sequence_no)
      .reduce(
        (current, next) => applyRuntimeEvent(current, next),
        [] as ChatItem[],
      );

    const hydrated = messagesToChatItems(
      [
        message({
          message_id: "user_1",
          role: "user",
          content_text: "Search docs",
        }),
        message({
          message_id: "assistant_row_1",
          role: "assistant",
          run_id: "run_123",
          content_text: "Final answer.",
          parent_message_id: "user_1",
        }),
      ],
      new Map([["run_123", replayEvents]]),
    );

    const hydratedAssistant = hydrated[1];
    const liveAssistant = liveItems[0];
    if (!hydratedAssistant || !liveAssistant) {
      throw new Error("Expected hydrated assistant message");
    }
    expect(hydrated).toHaveLength(2);
    expect(hydratedAssistant).toMatchObject({
      ...liveAssistant,
      id: "assistant_row_1",
      parentId: "user_1",
    });
    expect(
      firstThreadMessage([hydratedAssistant]).metadata?.timing,
    ).toMatchObject({
      totalStreamTime: 2000,
      tokenCount: 8,
      totalChunks: 4,
    });
  });

  it("falls back to stored assistant text when replay is unavailable", () => {
    const items = messagesToChatItems([
      message({
        message_id: "assistant_row_1",
        role: "assistant",
        run_id: "run_123",
        content_text: "Stored final answer.",
        metadata: { performance_metrics: performanceMetrics },
      }),
    ]);

    const storedAssistant = items[0];
    if (!storedAssistant) {
      throw new Error("Expected stored assistant message");
    }
    expect(storedAssistant).toMatchObject({
      id: "assistant_row_1",
      kind: "message",
      role: "assistant",
      runId: "run_123",
      content: [{ type: "text", text: "Stored final answer." }],
    });
    expect(
      firstThreadMessage([storedAssistant]).metadata?.timing,
    ).toMatchObject({
      totalStreamTime: 2000,
      tokenCount: 8,
      totalChunks: 4,
    });
  });

  it("hydrates parent links, attachments, quote metadata, and branch fields", () => {
    const [item] = messagesToChatItems([
      message({
        message_id: "user_2",
        role: "user",
        content_text: "Use this file",
        content: [{ type: "text", text: "Use this file" }],
        attachments: [
          {
            id: "attachment_1",
            type: "document",
            name: "brief.txt",
            content_type: "text/plain",
            content: [{ type: "text", text: "brief" }],
          },
        ],
        quote: { text: "quoted selection" },
        metadata: { source: "composer" },
        parent_message_id: "assistant_1",
        source_message_id: "user_1",
        branch_id: "branch_1",
      }),
    ]);
    const [threadMessage] = chatItemsToThreadMessages([item], null);

    expect(threadMessage.parentId).toBe("assistant_1");
    expect(threadMessage.attachments?.[0]).toMatchObject({
      id: "attachment_1",
      name: "brief.txt",
      contentType: "text/plain",
    });
    expect(threadMessage.metadata?.custom).toMatchObject({
      source: "composer",
      quote: { text: "quoted selection" },
      source_message_id: "user_1",
      branch_id: "branch_1",
    });
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
    expect(firstThreadMessage(items).status).toEqual({
      type: "requires-action",
      reason: "interrupt",
    });

    items = resolveApprovalDecision(items, "approval_123", "approved");

    expect(toolPart(items, "approval_request")?.result).toEqual({
      approval_id: "approval_123",
      decision: "approved",
    });
    expect(firstThreadMessage(items).status).toEqual({
      type: "requires-action",
      reason: "interrupt",
    });

    items = resolveMcpAuthSkip(items, "server_123");

    expect(toolPart(items, "mcp_auth_required")?.result).toEqual({
      server_id: "server_123",
      decision: "skipped",
    });
    expect(firstThreadMessage(items).status).toEqual({
      type: "running",
    });
  });

  it("preserves structured MCP output for result previews", () => {
    let items: ChatItem[] = [];
    const output = {
      content: [
        {
          type: "text",
          text: JSON.stringify({
            overview: "Found 1 result. Types include: task.",
            results: [{ name: "Follow up", status: "to do" }],
          }),
        },
      ],
    };

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "mcp_call_1",
        event_type: "tool_call_started",
        activity_kind: "tool",
        span_id: "call_mcp_123",
        payload: {
          tool_name: "call_mcp_tool",
          call_id: "call_mcp_123",
          args: {
            server_name: "mcp_clickup_com",
            tool_name: "clickup_search",
            arguments: { query: "pending tasks" },
          },
        },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "mcp_result_1",
        event_type: "tool_result",
        activity_kind: "tool",
        span_id: "call_mcp_123",
        payload: {
          tool_name: "call_mcp_tool",
          call_id: "call_mcp_123",
          output,
        },
      }),
    );

    expect(toolPart(items, "call_mcp_tool")?.result).toEqual(output);
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
