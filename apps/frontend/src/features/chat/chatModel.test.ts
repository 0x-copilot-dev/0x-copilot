import type {
  Message,
  McpServer,
  RuntimeEventEnvelope,
} from "@enterprise-search/api-types";
import type { ThreadMessageLike } from "@assistant-ui/react";
import { describe, expect, it } from "vitest";
import {
  applyRuntimeEvent,
  chatItemsToThreadMessages,
  messagesToChatItems,
  resolveApprovalDecision,
  resolveAuthenticatedMcpServers,
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

function mcpServer(overrides: Partial<McpServer>): McpServer {
  return {
    server_id: "server_123",
    name: "mcp_server",
    display_name: "MCP Server",
    url: "https://example.test/mcp",
    transport: "http",
    auth_mode: "oauth2",
    auth_state: "unauthenticated",
    health: "healthy",
    enabled: true,
    oauth_client_configured: true,
    created_at: "2026-04-30T00:00:00Z",
    updated_at: "2026-04-30T00:00:00Z",
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

function textParts(items: ChatItem[]): string[] {
  return assistantMessage(items).content.flatMap((part) =>
    part.type === "text" ? [part.text] : [],
  );
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

function applyEventSequence(
  events: Partial<RuntimeEventEnvelope>[],
): ChatItem[] {
  return events.reduce(
    (current, nextEvent) => applyRuntimeEvent(current, event(nextEvent)),
    [] as ChatItem[],
  );
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

  it("places final text after later tool calls without replacing checkpoints", () => {
    let items: ChatItem[] = [];

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "checkpoint_1",
        sequence_no: 1,
        event_type: "model_delta",
        activity_kind: "message",
        payload: { delta: "I need to inspect the workspace first." },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "tool_1",
        sequence_no: 2,
        event_type: "tool_call_started",
        activity_kind: "tool",
        span_id: "call_123",
        payload: {
          tool_name: "read_file",
          call_id: "call_123",
          status: "running",
          args: { path: "README.md" },
        },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "final_1",
        sequence_no: 3,
        event_type: "final_response",
        activity_kind: "message",
        status: "completed",
        payload: { message: "Final answer after the tool call." },
      }),
    );

    expect(textParts(items)).toEqual([
      "I need to inspect the workspace first.",
      "Final answer after the tool call.",
    ]);
    expect(
      assistantMessage(items).content.map((part) =>
        part.type === "tool-call" ? part.toolName : part.type,
      ),
    ).toEqual(["text", "read_file", "text"]);
  });

  it("carries generated presentation metadata onto tool parts", () => {
    const items = applyRuntimeEvent(
      [],
      event({
        event_id: "tool_1",
        event_type: "tool_call_started",
        activity_kind: "tool",
        span_id: "call_123",
        presentation: {
          title: "Searched the web",
          summary: "Found official Slack MCP sources.",
          status_label: "Done",
          kind: "result",
          result_preview: [
            {
              title: "Slack Developer Docs",
              subtitle: "Official MCP overview",
              url: "https://docs.slack.dev/ai/slack-mcp-server/",
              badge: "Official",
            },
          ],
          debug_label: "Tool details",
          confidence: "high",
        },
        payload: {
          tool_name: "web_search",
          call_id: "call_123",
          status: "completed",
          args: { query: "Slack MCP setup" },
        },
      }),
    );

    expect(toolPart(items, "web_search")?.args).toMatchObject({
      presentation: {
        title: "Searched the web",
        status_label: "Done",
        kind: "result",
        result_preview: [
          {
            title: "Slack Developer Docs",
            badge: "Official",
          },
        ],
      },
    });
  });

  it("preserves richer presentation when later tool events are weaker", () => {
    let items: ChatItem[] = [];

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "tool_result_1",
        sequence_no: 1,
        event_type: "tool_result",
        activity_kind: "tool",
        span_id: "call_123",
        presentation: {
          title: "Searched ClickUp tasks",
          summary: "Found matching tasks assigned to Parth.",
          status_label: "Done",
          kind: "result",
          result_preview: [{ title: "Fix onboarding", badge: "Open" }],
          confidence: "high",
        },
        payload: {
          tool_name: "call_mcp_tool",
          call_id: "call_123",
          status: "completed",
          output: { results: [{ title: "Fix onboarding" }] },
        },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "tool_complete_1",
        sequence_no: 2,
        event_type: "tool_call_completed",
        activity_kind: "tool",
        span_id: "call_123",
        presentation: {
          title: "Checked source",
          summary: "Enterprise Search finished this step.",
          status_label: "Done",
          kind: "result",
          confidence: "low",
        },
        payload: {
          tool_name: "call_mcp_tool",
          call_id: "call_123",
          status: "completed",
        },
      }),
    );

    expect(toolPart(items, "call_mcp_tool")?.args).toMatchObject({
      presentation: {
        title: "Searched ClickUp tasks",
        result_preview: [{ title: "Fix onboarding", badge: "Open" }],
      },
    });
  });

  it("hides checkpoint scratchpad deltas from visible assistant text", () => {
    const items = applyRuntimeEvent(
      [],
      event({
        event_id: "checkpoint_1",
        event_type: "model_delta",
        activity_kind: "message",
        payload: {
          delta:
            "Checkpoint: I loaded the ClickUp MCP server and need to inspect tools.",
        },
      }),
    );

    expect(items).toEqual([]);
  });

  it("streams text after a tool into a new text part", () => {
    let items: ChatItem[] = [];

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "delta_1",
        sequence_no: 1,
        event_type: "model_delta",
        activity_kind: "message",
        payload: { delta: "First checkpoint." },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "tool_1",
        sequence_no: 2,
        event_type: "tool_call_started",
        activity_kind: "tool",
        span_id: "call_123",
        payload: {
          tool_name: "read_file",
          call_id: "call_123",
          status: "running",
          args: { path: "README.md" },
        },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "delta_2",
        sequence_no: 3,
        event_type: "model_delta",
        activity_kind: "message",
        payload: { delta: "Second checkpoint." },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "delta_3",
        sequence_no: 4,
        event_type: "model_delta",
        activity_kind: "message",
        payload: { delta: " More detail." },
      }),
    );

    expect(textParts(items)).toEqual([
      "First checkpoint.",
      "Second checkpoint. More detail.",
    ]);
    expect(
      assistantMessage(items).content.map((part) =>
        part.type === "tool-call" ? part.toolName : part.type,
      ),
    ).toEqual(["text", "read_file", "text"]);
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

  it("does not add large-result artifact paths to subagent activity", () => {
    let items: ChatItem[] = [];

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
        event_id: "large_result_1",
        event_type: "tool_call_completed",
        activity_kind: "tool",
        parent_task_id: "task_123",
        span_id: "call_large",
        payload: {
          tool_name: "grep",
          call_id: "call_large",
          status: "completed",
          args: { path: "/large_tool_results/call_large" },
        },
      }),
    );

    expect(toolPart(items, "run_subagent")?.args?.activities).toBeUndefined();
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

  it("hydrates replayed tool presentation metadata identically", () => {
    const replayEvents = [
      event({
        event_id: "tool_1",
        sequence_no: 1,
        event_type: "tool_call_completed",
        activity_kind: "tool",
        span_id: "call_123",
        presentation: {
          title: "Checked project files",
          summary: "Found one matching implementation file.",
          status_label: "Done",
          kind: "result",
          debug_label: "Tool details",
        },
        payload: {
          tool_name: "grep",
          call_id: "call_123",
          status: "completed",
          summary: "legacy fallback should not win",
        },
      }),
    ];

    const liveItems = replayEvents.reduce(
      (current, next) => applyRuntimeEvent(current, next),
      [] as ChatItem[],
    );
    const hydrated = messagesToChatItems(
      [
        message({
          message_id: "assistant_row_1",
          role: "assistant",
          run_id: "run_123",
          content_text: "",
        }),
      ],
      new Map([["run_123", replayEvents]]),
    );

    const hydratedTool =
      hydrated[0]?.kind === "message" ? hydrated[0] : undefined;
    if (!hydratedTool) {
      throw new Error("Expected hydrated assistant tool message");
    }
    const hydratedGrep = hydratedTool.content.find(
      (part): part is ThreadToolCallPart =>
        part.type === "tool-call" && part.toolName === "grep",
    );
    const liveGrep = toolPart(liveItems, "grep");
    if (!liveGrep?.args || !hydratedGrep?.args) {
      throw new Error("Expected live and hydrated grep tool parts");
    }
    expect(liveGrep.args.presentation).toEqual(hydratedGrep.args.presentation);
    expect(liveGrep.args.presentation).toMatchObject({
      title: "Checked project files",
      summary: "Found one matching implementation file.",
    });
  });

  it("preserves partial streamed assistant output when a failed run has no stored assistant row", () => {
    const replayEvents = [
      event({
        event_id: "delta_1",
        sequence_no: 1,
        event_type: "model_delta",
        activity_kind: "message",
        payload: { delta: "Partial answer before failure." },
      }),
      event({
        event_id: "failed_1",
        sequence_no: 2,
        event_type: "run_failed",
        activity_kind: "run",
        status: "failed",
        display_title: "Run failed",
        summary: "Subagent failed midway.",
        payload: { status: "run_failed" },
      }),
    ];

    const items = messagesToChatItems(
      [
        message({
          message_id: "user_1",
          role: "user",
          run_id: "run_123",
          content_text: "Research this",
        }),
      ],
      new Map([["run_123", replayEvents]]),
    );

    expect(items).toHaveLength(2);
    expect(textPart(items)).toBe("Partial answer before failure.");
    expect(assistantMessage(items)).toMatchObject({
      id: "assistant-run_123",
      parentId: "user_1",
      runId: "run_123",
      status: { type: "incomplete", reason: "error" },
    });
    expect(
      assistantMessage(items).content.some(
        (part) => part.type === "tool-call" && part.toolName === "run_progress",
      ),
    ).toBe(true);
    expect(firstThreadMessage([assistantMessage(items)]).status).toEqual({
      type: "incomplete",
      reason: "error",
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
      display_title: "Write a merge-sorted-lists function.",
      short_summary: "Write a merge-sorted-lists function.",
      subagent_name: "general-purpose",
      status: "completed",
      summary: "Done.",
      task_summary: "Write a merge-sorted-lists function.",
    });
    expect(subagent?.argsText).toBeUndefined();
    expect(subagent?.result).toBe("Done.");
    expect(
      assistantMessage(items).content.some(
        (part) =>
          part.type === "tool-call" && part.toolCallId === "namespace_1",
      ),
    ).toBe(false);
  });

  it("stores user-facing subagent presentation without raw prompt details", () => {
    const longPrompt = [
      "Create a formal research report on the phrase/concept 'health is wealth'.",
      "Investigate and synthesize evidence for how health affects economic outcomes.",
      "Provide: executive summary, mechanisms, evidence sections, policy implications, and references.",
    ].join(" ");

    const items = applyRuntimeEvent(
      [],
      event({
        event_id: "subagent_started_long",
        event_type: "subagent_started",
        activity_kind: "subagent",
        task_id: "call_report",
        subagent_id: "general-purpose",
        status: "queued",
        summary: longPrompt,
        payload: {
          task_id: "call_report",
          subagent_name: "general-purpose",
          status: "queued",
          summary: longPrompt,
          short_summary:
            "Preparing a formal research report on 'health is wealth'.",
          display_title:
            "Preparing a formal research report on 'health is wealth'.",
        },
      }),
    );

    const subagent = toolPart(items, "run_subagent");
    expect(subagent?.args).toMatchObject({
      display_title:
        "Preparing a formal research report on 'health is wealth'.",
      short_summary:
        "Preparing a formal research report on 'health is wealth'.",
      task_id: "call_report",
      task_summary: "Preparing a formal research report on 'health is wealth'.",
    });
    expect(subagent?.args).toHaveProperty("summary", longPrompt);
    expect(subagent?.argsText).toBeUndefined();
  });

  it("marks failed subagent events as errored without losing task presentation", () => {
    const items = applyRuntimeEvent(
      [],
      event({
        event_id: "subagent_failed_1",
        event_type: "subagent_completed",
        activity_kind: "subagent",
        task_id: "call_failed",
        subagent_id: "researcher",
        status: "failed",
        summary: "The research task failed.",
        payload: {
          task_id: "call_failed",
          subagent_name: "researcher",
          status: "failed",
          summary: "The research task failed.",
          short_summary: "Researching market data.",
          display_title: "Researching market data.",
        },
      }),
    );

    const subagent = toolPart(items, "run_subagent");
    expect(subagent?.isError).toBe(true);
    expect(subagent?.args).toMatchObject({
      display_title: "Researching market data.",
      status: "failed",
      task_summary: "Researching market data.",
    });
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
          approval_kind: "mcp_tool",
          server_name: "mcp_clickup_com",
          display_name: "ClickUp",
          tool_name: "list_tasks",
          arguments: { assignee: "me" },
          risk_level: "low",
          read_only: true,
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
          approval_id: "mcp_auth_123",
          action_id: "mcp_auth_123",
          approval_kind: "mcp_auth",
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
    expect(toolPart(items, "approval_request")?.args).toMatchObject({
      approval_kind: "mcp_tool",
      server_name: "mcp_clickup_com",
      tool_name: "list_tasks",
      arguments: { assignee: "me" },
      risk_level: "low",
      read_only: true,
    });
    expect(toolPart(items, "mcp_auth_required")?.toolCallId).toBe(
      "mcp_auth_123",
    );
    expect(firstThreadMessage(items).status).toEqual({
      type: "requires-action",
      reason: "interrupt",
    });

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "final_while_waiting",
        event_type: "final_response",
        activity_kind: "message",
        status: "completed",
        payload: {
          message: "Authenticate here, then tell me when you are done.",
        },
      }),
    );

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

    items = resolveMcpAuthSkip(items, "mcp_auth_123");

    expect(toolPart(items, "mcp_auth_required")?.result).toEqual({
      approval_id: "mcp_auth_123",
      server_id: "server_123",
      decision: "skipped",
    });
    expect(firstThreadMessage(items).status).toEqual({
      type: "running",
    });
  });

  it("resolves stale MCP auth cards when the connector becomes authenticated", () => {
    let items: ChatItem[] = [];

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "mcp_1",
        event_type: "mcp_auth_required",
        activity_kind: "mcp_auth",
        payload: {
          approval_id: "mcp_auth_123",
          action_id: "mcp_auth_123",
          approval_kind: "mcp_auth",
          server_id: "server_123",
          server_name: "mcp_clickup_com",
          display_name: "ClickUp",
          auth_url: "https://example.test/old-auth",
          expires_at: "2026-04-30T01:00:00Z",
          message: "Connect ClickUp",
        },
      }),
    );

    items = resolveAuthenticatedMcpServers(items, [
      mcpServer({
        server_id: "server_123",
        name: "mcp_clickup_com",
        display_name: "ClickUp",
        auth_state: "authenticated",
      }),
    ]);

    expect(toolPart(items, "mcp_auth_required")?.result).toEqual({
      approval_id: "mcp_auth_123",
      server_id: "server_123",
      decision: "approved",
    });
    expect(firstThreadMessage(items).status).toEqual({
      type: "running",
    });
  });

  it("removes stale auth_mcp wrapper calls when auth finishes", () => {
    let items: ChatItem[] = [];

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "auth_wrapper_1",
        event_type: "tool_call_started",
        activity_kind: "tool",
        span_id: "call_auth_mcp_123",
        payload: {
          tool_name: "auth_mcp",
          call_id: "call_auth_mcp_123",
          args: {
            server_id: "server_123",
            server_name: "mcp_clickup_com",
          },
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
          approval_id: "mcp_auth_123",
          action_id: "mcp_auth_123",
          approval_kind: "mcp_auth",
          server_id: "server_123",
          server_name: "mcp_clickup_com",
          display_name: "ClickUp",
          auth_url: "https://example.test/old-auth",
          expires_at: "2026-04-30T01:00:00Z",
          message: "Connect ClickUp",
        },
      }),
    );

    items = resolveAuthenticatedMcpServers(items, [
      mcpServer({
        server_id: "server_123",
        name: "mcp_clickup_com",
        display_name: "ClickUp",
        auth_state: "authenticated",
      }),
    ]);

    expect(toolPart(items, "auth_mcp")).toBeUndefined();
    expect(toolPart(items, "mcp_auth_required")?.result).toEqual({
      approval_id: "mcp_auth_123",
      server_id: "server_123",
      decision: "approved",
    });
  });

  it("replaces correlated MCP wrapper calls with approval cards", () => {
    let items: ChatItem[] = [];

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "mcp_call_approval_1",
        event_type: "tool_call_started",
        activity_kind: "tool",
        span_id: "call_mcp_approval_123",
        payload: {
          tool_name: "call_mcp_tool",
          call_id: "call_mcp_approval_123",
          args: {
            server_name: "mcp_clickup_com",
            tool_name: "clickup_filter_tasks",
            arguments: { assignees: ["me"] },
          },
        },
      }),
    );
    expect(toolPart(items, "call_mcp_tool")?.toolCallId).toBe(
      "call_mcp_approval_123",
    );

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "approval_from_tool_1",
        event_type: "approval_requested",
        activity_kind: "approval",
        payload: {
          approval_id: "approval_json_123",
          approval_kind: "mcp_tool",
          source_tool_call_id: "call_mcp_approval_123",
          server_name: "mcp_clickup_com",
          display_name: "ClickUp",
          tool_name: "clickup_filter_tasks",
          arguments: { assignees: ["me"] },
          risk_level: "medium",
          read_only: true,
          message: "Approve ClickUp to run clickup_filter_tasks.",
        },
      }),
    );

    expect(toolPart(items, "call_mcp_tool")).toBeUndefined();
    expect(toolPart(items, "approval_request")?.toolCallId).toBe(
      "approval_json_123",
    );
    expect(assistantMessage(items).content).toHaveLength(1);
  });

  it("replaces matching MCP wrapper calls with approval cards without a source id", () => {
    let items: ChatItem[] = [];

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "mcp_call_approval_1",
        event_type: "tool_call_started",
        activity_kind: "tool",
        span_id: "call_mcp_approval_123",
        payload: {
          tool_name: "call_mcp_tool",
          call_id: "call_mcp_approval_123",
          args: {
            server_name: "mcp_clickup_com",
            tool_name: "clickup_search",
            arguments: {},
          },
        },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "approval_from_interrupt_1",
        event_type: "approval_requested",
        activity_kind: "approval",
        payload: {
          approval_id: "approval_json_123",
          approval_kind: "mcp_tool",
          server_name: "mcp_clickup_com",
          display_name: "ClickUp",
          tool_name: "clickup_search",
          arguments: {},
          risk_level: "low",
          read_only: true,
          message: "Allow ClickUp search?",
        },
      }),
    );

    expect(toolPart(items, "call_mcp_tool")).toBeUndefined();
    expect(toolPart(items, "approval_request")?.toolCallId).toBe(
      "approval_json_123",
    );
    expect(assistantMessage(items).content).toHaveLength(1);
  });

  it("replaces MCP approval wrapper calls in place", () => {
    let items: ChatItem[] = [];

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "before_approval",
        sequence_no: 1,
        event_type: "model_delta",
        activity_kind: "message",
        payload: { delta: "I need connector data." },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "mcp_call_approval_1",
        sequence_no: 2,
        event_type: "tool_call_started",
        activity_kind: "tool",
        span_id: "call_mcp_approval_123",
        payload: {
          tool_name: "call_mcp_tool",
          call_id: "call_mcp_approval_123",
          args: {
            server_name: "mcp_clickup_com",
            tool_name: "clickup_filter_tasks",
            arguments: { assignees: ["me"] },
          },
        },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "after_wrapper",
        sequence_no: 3,
        event_type: "model_delta",
        activity_kind: "message",
        payload: { delta: "Waiting for consent." },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "approval_from_tool_1",
        sequence_no: 4,
        event_type: "approval_requested",
        activity_kind: "approval",
        payload: {
          approval_id: "approval_json_123",
          approval_kind: "mcp_tool",
          source_tool_call_id: "call_mcp_approval_123",
          server_name: "mcp_clickup_com",
          display_name: "ClickUp",
          tool_name: "clickup_filter_tasks",
          arguments: { assignees: ["me"] },
          risk_level: "medium",
          read_only: true,
          message: "Approve ClickUp to run clickup_filter_tasks.",
        },
      }),
    );

    expect(
      assistantMessage(items).content.map((part) =>
        part.type === "tool-call" ? part.toolName : part.type,
      ),
    ).toEqual(["text", "approval_request", "text"]);
  });

  it("replaces MCP auth wrapper calls in place", () => {
    let items: ChatItem[] = [];

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "before_auth",
        sequence_no: 1,
        event_type: "model_delta",
        activity_kind: "message",
        payload: { delta: "I need connector access." },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "mcp_auth_call_1",
        sequence_no: 2,
        event_type: "tool_call_started",
        activity_kind: "tool",
        span_id: "call_auth_mcp_123",
        payload: {
          tool_name: "auth_mcp",
          call_id: "call_auth_mcp_123",
          args: {
            server_name: "drive_mcp",
            server_id: "server_123",
          },
        },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "after_wrapper",
        sequence_no: 3,
        event_type: "model_delta",
        activity_kind: "message",
        payload: { delta: "Waiting for authentication." },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "auth_from_tool_1",
        sequence_no: 4,
        event_type: "mcp_auth_required",
        activity_kind: "mcp_auth",
        payload: {
          approval_id: "mcp_auth_123",
          action_id: "mcp_auth_123",
          approval_kind: "mcp_auth",
          source_tool_call_id: "call_auth_mcp_123",
          server_id: "server_123",
          server_name: "drive_mcp",
          display_name: "Drive MCP",
          auth_url: "https://example.test/auth",
          expires_at: "2026-04-30T01:00:00Z",
          message: "Connect Drive MCP",
        },
      }),
    );

    expect(
      assistantMessage(items).content.map((part) =>
        part.type === "tool-call" ? part.toolName : part.type,
      ),
    ).toEqual(["text", "mcp_auth_required", "text"]);
  });

  it("replaces matching MCP auth wrapper calls without a source id", () => {
    let items: ChatItem[] = [];

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "mcp_auth_call_1",
        event_type: "tool_call_started",
        activity_kind: "tool",
        span_id: "call_auth_mcp_123",
        payload: {
          tool_name: "auth_mcp",
          call_id: "call_auth_mcp_123",
          args: {
            server_name: "drive_mcp",
            server_id: "server_123",
          },
        },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "auth_from_interrupt_1",
        event_type: "mcp_auth_required",
        activity_kind: "mcp_auth",
        payload: {
          approval_id: "mcp_auth_123",
          action_id: "mcp_auth_123",
          approval_kind: "mcp_auth",
          server_id: "server_123",
          server_name: "drive_mcp",
          display_name: "Drive MCP",
          auth_url: "https://example.test/auth",
          expires_at: "2026-04-30T01:00:00Z",
          message: "Connect Drive MCP",
        },
      }),
    );

    expect(
      assistantMessage(items).content.map((part) =>
        part.type === "tool-call" ? part.toolName : part.type,
      ),
    ).toEqual(["mcp_auth_required"]);
  });

  it("carries generated presentation metadata onto approval cards", () => {
    const items = applyRuntimeEvent(
      [],
      event({
        event_id: "approval_1",
        event_type: "approval_requested",
        activity_kind: "approval",
        presentation: {
          title: "Allow ClickUp search?",
          summary: "Enterprise Search wants to search ClickUp tasks.",
          status_label: "Waiting for permission",
          kind: "approval",
          group_key: "call_123",
          debug_label: "Tool details",
          confidence: "high",
        },
        payload: {
          approval_id: "approval_123",
          approval_kind: "mcp_tool",
          server_name: "mcp_clickup_com",
          tool_name: "clickup_search",
          status: "pending",
        },
      }),
    );

    expect(toolPart(items, "approval_request")?.args).toMatchObject({
      presentation: {
        title: "Allow ClickUp search?",
        summary: "Enterprise Search wants to search ClickUp tasks.",
        status_label: "Waiting for permission",
        kind: "approval",
      },
    });
  });

  it("does not append new activity while any action is pending", () => {
    let items: ChatItem[] = [];

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "approval_1",
        event_type: "approval_requested",
        activity_kind: "approval",
        payload: {
          approval_id: "approval_123",
          approval_kind: "mcp_tool",
          server_name: "mcp_clickup_com",
          display_name: "ClickUp",
          tool_name: "list_tasks",
          arguments: { assignee: "me" },
          risk_level: "low",
          read_only: true,
          message: "Approve the tool call?",
        },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "late_tool",
        event_type: "tool_call_started",
        activity_kind: "tool",
        span_id: "late_call",
        payload: {
          tool_name: "read_file",
          call_id: "late_call",
          status: "running",
          args: { path: "README.md" },
        },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "late_delta",
        event_type: "model_delta",
        activity_kind: "message",
        payload: { delta: "Late text." },
      }),
    );

    expect(
      assistantMessage(items).content.map((part) =>
        part.type === "tool-call" ? part.toolName : part.type,
      ),
    ).toEqual(["approval_request"]);

    items = resolveApprovalDecision(items, "approval_123", "approved");
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "after_approval",
        event_type: "model_delta",
        activity_kind: "message",
        payload: { delta: "Approved text." },
      }),
    );

    expect(textParts(items)).toEqual(["Approved text."]);
  });

  it("does not append new activity while MCP auth is pending", () => {
    let items: ChatItem[] = [];

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "mcp_auth_1",
        event_type: "mcp_auth_required",
        activity_kind: "mcp_auth",
        payload: {
          approval_id: "mcp_auth_123",
          action_id: "mcp_auth_123",
          approval_kind: "mcp_auth",
          server_id: "server_123",
          server_name: "drive_mcp",
          display_name: "Drive MCP",
          auth_url: "https://example.test/auth",
          expires_at: "2026-04-30T01:00:00Z",
          message: "Connect Drive MCP",
        },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "late_tool",
        event_type: "tool_call_started",
        activity_kind: "tool",
        span_id: "late_call",
        payload: {
          tool_name: "read_file",
          call_id: "late_call",
          status: "running",
          args: { path: "README.md" },
        },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "late_delta",
        event_type: "model_delta",
        activity_kind: "message",
        payload: { delta: "Late text." },
      }),
    );

    expect(
      assistantMessage(items).content.map((part) =>
        part.type === "tool-call" ? part.toolName : part.type,
      ),
    ).toEqual(["mcp_auth_required"]);

    items = resolveMcpAuthSkip(items, "mcp_auth_123");
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "after_auth",
        event_type: "model_delta",
        activity_kind: "message",
        payload: { delta: "Skipped auth." },
      }),
    );

    expect(textParts(items)).toEqual(["Skipped auth."]);
  });

  it("resolves pending MCP auth from approval resolved events", () => {
    let items: ChatItem[] = [];

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "mcp_auth_1",
        sequence_no: 1,
        event_type: "mcp_auth_required",
        activity_kind: "mcp_auth",
        payload: {
          approval_id: "mcp_auth_123",
          action_id: "mcp_auth_123",
          approval_kind: "mcp_auth",
          server_id: "server_123",
          server_name: "drive_mcp",
          display_name: "Drive MCP",
          auth_url: "https://example.test/auth",
          expires_at: "2026-04-30T01:00:00Z",
          message: "Connect Drive MCP",
        },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "auth_resolved_1",
        sequence_no: 2,
        event_type: "approval_resolved",
        activity_kind: "approval",
        payload: {
          approval_id: "mcp_auth_123",
          approval_kind: "mcp_auth",
          status: "approved",
        },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "final_after_auth",
        sequence_no: 3,
        event_type: "final_response",
        activity_kind: "message",
        status: "completed",
        payload: { message: "Drive MCP is connected." },
      }),
    );

    expect(toolPart(items, "mcp_auth_required")?.result).toEqual({
      approval_id: "mcp_auth_123",
      server_id: "server_123",
      decision: "approved",
    });
    expect(textParts(items)).toEqual(["Drive MCP is connected."]);
  });

  it("does not expose streamed tool arg deltas in display text", () => {
    let items: ChatItem[] = [];

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "mcp_call_delta_1",
        event_type: "tool_call_started",
        activity_kind: "tool",
        span_id: "call_mcp_delta_123",
        payload: {
          tool_name: "call_mcp_tool",
          call_id: "call_mcp_delta_123",
          args: {
            server_name: "mcp_clickup_com",
          },
        },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "mcp_call_delta_2",
        event_type: "tool_call_delta",
        activity_kind: "tool",
        span_id: "call_mcp_delta_123",
        payload: {
          tool_name: "call_mcp_tool",
          call_id: "call_mcp_delta_123",
          delta: '{"server_name":"mcp_clickup_com"}',
          args_delta: {
            tool_name: "clickup_filter_tasks",
            arguments: { assignees: ["me"] },
          },
          status: "running",
        },
      }),
    );

    const mcpTool = toolPart(items, "call_mcp_tool");
    expect(mcpTool?.args).not.toHaveProperty("deltas");
    expect(mcpTool?.args).not.toHaveProperty("delta");
    expect(mcpTool?.argsText).toContain("clickup_filter_tasks");
    expect(mcpTool?.argsText).not.toContain("deltas");
    expect(mcpTool?.argsText).not.toContain("delta");
    expect(mcpTool?.argsText).not.toContain("status");
  });

  it("hides virtual large-result read and search tool calls", () => {
    let items: ChatItem[] = [];

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "large_read_1",
        event_type: "tool_call_started",
        activity_kind: "tool",
        span_id: "call_large_read",
        payload: {
          tool_name: "read_file",
          call_id: "call_large_read",
          args: {
            file_path: "/large_tool_results/call_OSm333FzbeC5JRHDhiu6DnRP",
            offset: 0,
            limit: 120,
          },
        },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "large_search_1",
        event_type: "tool_call_started",
        activity_kind: "tool",
        span_id: "call_large_search",
        payload: {
          tool_name: "rg",
          call_id: "call_large_search",
          args: {
            pattern: "clickup_resolve_assignees",
            path: "/large_tool_results/call_OSm333FzbeC5JRHDhiu6DnRP",
            output_mode: "files_with_matches",
          },
        },
      }),
    );

    expect(items).toEqual([]);
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

  describe("runtime flow matrix", () => {
    it("settles a simple message flow through final response and run completion", () => {
      const items = applyEventSequence([
        {
          event_id: "run_started_1",
          sequence_no: 1,
          event_type: "run_started",
          activity_kind: "run",
        },
        {
          event_id: "delta_1",
          sequence_no: 2,
          event_type: "model_delta",
          activity_kind: "message",
          payload: { delta: "Draft answer." },
        },
        {
          event_id: "final_1",
          sequence_no: 3,
          event_type: "final_response",
          activity_kind: "message",
          status: "completed",
          payload: { message: "Final answer." },
        },
        {
          event_id: "completed_1",
          sequence_no: 4,
          event_type: "run_completed",
          activity_kind: "run",
          status: "completed",
        },
      ]);

      expect(textPart(items)).toBe("Final answer.");
      expect(firstThreadMessage(items).status).toEqual({
        type: "complete",
        reason: "stop",
      });
    });

    it("marks a streamed message complete when run_completed arrives without final_response", () => {
      const items = applyEventSequence([
        {
          event_id: "delta_1",
          sequence_no: 1,
          event_type: "model_delta",
          activity_kind: "message",
          payload: { delta: "Partial but valid answer." },
        },
        {
          event_id: "completed_1",
          sequence_no: 2,
          event_type: "run_completed",
          activity_kind: "run",
          status: "completed",
        },
      ]);

      expect(textPart(items)).toBe("Partial but valid answer.");
      expect(firstThreadMessage(items).status).toEqual({
        type: "complete",
        reason: "stop",
      });
    });

    it("handles text, a tool call, stale tool status, and final response", () => {
      const items = applyEventSequence([
        {
          event_id: "delta_1",
          sequence_no: 1,
          event_type: "model_delta",
          activity_kind: "message",
          payload: { delta: "Checking source." },
        },
        {
          event_id: "tool_started_1",
          sequence_no: 2,
          event_type: "tool_call_started",
          activity_kind: "tool",
          span_id: "call_123",
          payload: {
            tool_name: "read_file",
            call_id: "call_123",
            args: { path: "README.md" },
            status: "running",
          },
        },
        {
          event_id: "tool_result_1",
          sequence_no: 3,
          event_type: "tool_result",
          activity_kind: "tool",
          span_id: "call_123",
          status: "running",
          payload: {
            tool_name: "read_file",
            call_id: "call_123",
            output: { content: "docs" },
          },
        },
        {
          event_id: "tool_complete_1",
          sequence_no: 4,
          event_type: "tool_call_completed",
          activity_kind: "tool",
          span_id: "call_123",
          status: "running",
          payload: {
            tool_name: "read_file",
            call_id: "call_123",
            status: "completed",
          },
        },
        {
          event_id: "final_1",
          sequence_no: 5,
          event_type: "final_response",
          activity_kind: "message",
          status: "completed",
          payload: { message: "Read the docs." },
        },
      ]);

      expect(toolPart(items, "read_file")?.result).toEqual({ content: "docs" });
      expect(textParts(items)).toEqual(["Checking source.", "Read the docs."]);
      expect(firstThreadMessage(items).status).toEqual({
        type: "complete",
        reason: "stop",
      });
    });

    it("handles a subagent lifecycle followed by a final response", () => {
      const items = applyEventSequence([
        {
          event_id: "subagent_started_1",
          sequence_no: 1,
          event_type: "subagent_started",
          activity_kind: "subagent",
          task_id: "task_123",
          status: "queued",
          summary: "Research ClickUp tasks.",
          payload: {
            task_id: "task_123",
            subagent_name: "researcher",
            status: "queued",
            summary: "Research ClickUp tasks.",
          },
        },
        {
          event_id: "subagent_progress_1",
          sequence_no: 2,
          event_type: "subagent_progress",
          activity_kind: "subagent",
          task_id: "task_123",
          status: "running",
          summary: "Looking through task results.",
          payload: {
            task_id: "task_123",
            status: "running",
            summary: "Looking through task results.",
          },
        },
        {
          event_id: "subagent_completed_1",
          sequence_no: 3,
          event_type: "subagent_completed",
          activity_kind: "subagent",
          task_id: "task_123",
          status: "completed",
          summary: "Found matching task details.",
          payload: {
            task_id: "task_123",
            status: "completed",
            summary: "Found matching task details.",
          },
        },
        {
          event_id: "final_1",
          sequence_no: 4,
          event_type: "final_response",
          activity_kind: "message",
          status: "completed",
          payload: { message: "Here are the task details." },
        },
      ]);

      expect(toolPart(items, "run_subagent")?.result).toBe(
        "Found matching task details.",
      );
      expect(textPart(items)).toBe("Here are the task details.");
      expect(firstThreadMessage(items).status).toEqual({
        type: "complete",
        reason: "stop",
      });
    });

    it("attaches nested tool activity to a subagent", () => {
      const items = applyEventSequence([
        {
          event_id: "subagent_started_1",
          sequence_no: 1,
          event_type: "subagent_started",
          activity_kind: "subagent",
          task_id: "task_123",
          status: "queued",
          summary: "Research ClickUp tasks.",
          payload: {
            task_id: "task_123",
            subagent_name: "researcher",
            status: "queued",
            summary: "Research ClickUp tasks.",
          },
        },
        {
          event_id: "nested_tool_1",
          sequence_no: 2,
          event_type: "tool_result",
          activity_kind: "tool",
          parent_task_id: "task_123",
          span_id: "call_nested",
          status: "completed",
          payload: {
            tool_name: "search_docs",
            call_id: "call_nested",
            output: { count: 2 },
          },
        },
      ]);

      const subagent = toolPart(items, "run_subagent");
      expect(subagent?.args).toMatchObject({
        activities: [
          {
            id: "call_nested",
            kind: "tool",
            status: "completed",
            result: "1 fields returned",
          },
        ],
      });
    });

    it("handles MCP tool approval without OAuth auth", () => {
      const items = applyEventSequence([
        {
          event_id: "approval_1",
          sequence_no: 1,
          event_type: "approval_requested",
          activity_kind: "approval",
          payload: {
            approval_id: "approval_123",
            approval_kind: "mcp_tool",
            server_name: "mcp_clickup_com",
            display_name: "ClickUp",
            tool_name: "clickup_filter_tasks",
            arguments: { assignee: "Parth" },
          },
        },
        {
          event_id: "approval_resolved_1",
          sequence_no: 2,
          event_type: "approval_resolved",
          activity_kind: "approval",
          payload: {
            approval_id: "approval_123",
            status: "approved",
          },
        },
        {
          event_id: "mcp_result_1",
          sequence_no: 3,
          event_type: "tool_result",
          activity_kind: "tool",
          span_id: "call_mcp_123",
          payload: {
            tool_name: "call_mcp_tool",
            call_id: "call_mcp_123",
            output: { tasks: [{ name: "Follow up" }] },
          },
        },
        {
          event_id: "final_1",
          sequence_no: 4,
          event_type: "final_response",
          activity_kind: "message",
          status: "completed",
          payload: { message: "Found one task." },
        },
      ]);

      expect(toolPart(items, "approval_request")?.result).toEqual({
        approval_id: "approval_123",
        decision: "approved",
      });
      expect(toolPart(items, "call_mcp_tool")?.result).toEqual({
        tasks: [{ name: "Follow up" }],
      });
      expect(textPart(items)).toBe("Found one task.");
    });

    it("handles MCP auth resolution and continued final response", () => {
      const items = applyEventSequence([
        {
          event_id: "mcp_auth_1",
          sequence_no: 1,
          event_type: "mcp_auth_required",
          activity_kind: "mcp_auth",
          payload: {
            approval_id: "mcp_auth_123",
            action_id: "mcp_auth_123",
            approval_kind: "mcp_auth",
            server_id: "server_123",
            server_name: "mcp_clickup_com",
            display_name: "ClickUp",
            auth_url: "https://example.test/auth",
            expires_at: "2026-04-30T01:00:00Z",
            message: "Connect ClickUp",
          },
        },
        {
          event_id: "mcp_auth_resolved_1",
          sequence_no: 2,
          event_type: "approval_resolved",
          activity_kind: "approval",
          payload: {
            approval_id: "mcp_auth_123",
            status: "approved",
          },
        },
        {
          event_id: "final_1",
          sequence_no: 3,
          event_type: "final_response",
          activity_kind: "message",
          status: "completed",
          payload: { message: "Connector is ready." },
        },
      ]);

      expect(toolPart(items, "mcp_auth_required")?.result).toMatchObject({
        approval_id: "mcp_auth_123",
        decision: "approved",
      });
      expect(textPart(items)).toBe("Connector is ready.");
      expect(firstThreadMessage(items).status).toEqual({
        type: "complete",
        reason: "stop",
      });
    });

    it("handles a mixed text, tool, approval, result, and final response flow", () => {
      const items = applyEventSequence([
        {
          event_id: "delta_1",
          sequence_no: 1,
          event_type: "model_delta",
          activity_kind: "message",
          payload: { delta: "I will check ClickUp." },
        },
        {
          event_id: "tool_started_1",
          sequence_no: 2,
          event_type: "tool_call_started",
          activity_kind: "tool",
          span_id: "call_mcp_123",
          payload: {
            tool_name: "call_mcp_tool",
            call_id: "call_mcp_123",
            args: {
              server_name: "mcp_clickup_com",
              tool_name: "clickup_filter_tasks",
            },
          },
        },
        {
          event_id: "approval_1",
          sequence_no: 3,
          event_type: "approval_requested",
          activity_kind: "approval",
          payload: {
            approval_id: "approval_123",
            approval_kind: "mcp_tool",
            server_name: "mcp_clickup_com",
            tool_name: "clickup_filter_tasks",
          },
        },
        {
          event_id: "approval_resolved_1",
          sequence_no: 4,
          event_type: "approval_resolved",
          activity_kind: "approval",
          payload: {
            approval_id: "approval_123",
            status: "approved",
          },
        },
        {
          event_id: "tool_result_1",
          sequence_no: 5,
          event_type: "tool_result",
          activity_kind: "tool",
          span_id: "call_mcp_123",
          payload: {
            tool_name: "call_mcp_tool",
            call_id: "call_mcp_123",
            output: { tasks: [] },
          },
        },
        {
          event_id: "final_1",
          sequence_no: 6,
          event_type: "final_response",
          activity_kind: "message",
          status: "completed",
          payload: { message: "No matching tasks found." },
        },
      ]);

      expect(textParts(items)).toEqual([
        "I will check ClickUp.",
        "No matching tasks found.",
      ]);
      expect(toolPart(items, "approval_request")?.result).toEqual({
        approval_id: "approval_123",
        decision: "approved",
      });
      expect(toolPart(items, "call_mcp_tool")?.result).toEqual({ tasks: [] });
    });

    it("allows run failure to settle the message while approval is pending", () => {
      const items = applyEventSequence([
        {
          event_id: "approval_1",
          sequence_no: 1,
          event_type: "approval_requested",
          activity_kind: "approval",
          payload: {
            approval_id: "approval_123",
            approval_kind: "mcp_tool",
            server_name: "mcp_clickup_com",
            tool_name: "clickup_filter_tasks",
          },
        },
        {
          event_id: "failed_1",
          sequence_no: 2,
          event_type: "run_failed",
          activity_kind: "run",
          status: "failed",
          display_title: "Run failed",
          summary: "The connector failed while waiting.",
          payload: { status: "failed" },
        },
      ]);

      expect(firstThreadMessage(items).status).toEqual({
        type: "incomplete",
        reason: "error",
      });
      expect(toolPart(items, "run_progress")?.isError).toBe(true);
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
