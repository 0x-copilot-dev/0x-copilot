import type { RuntimeEventEnvelope } from "@enterprise-search/api-types";
import { describe, expect, it } from "vitest";
import { applyRuntimeEvent } from "./eventReducer";
import type { ChatItem } from "./types";

function event(overrides: Partial<RuntimeEventEnvelope>): RuntimeEventEnvelope {
  return {
    event_id: "event_123",
    run_id: "run_123",
    conversation_id: "conversation_123",
    sequence_no: 1,
    event_type: "model_delta",
    activity_kind: "message",
    status: "running",
    payload: { delta: "Planning resumed." },
    created_at: "2026-04-30T00:00:00Z",
    ...overrides,
  };
}

function assistantWithDiscoverySuggestion(): ChatItem {
  return {
    id: "assistant-run_123",
    kind: "message",
    role: "assistant",
    runId: "run_123",
    content: [
      {
        type: "tool-call",
        toolCallId: "mcp_discovery_123",
        toolName: "mcp_auth_required",
        args: {
          approval_id: "mcp_discovery_123",
          discovery_reason: "tool_may_help",
          server_id: "linear",
          status: "waiting",
        },
      },
    ],
  } as ChatItem;
}

describe("applyRuntimeEvent", () => {
  it("continues applying run events while optional MCP discovery is unresolved", () => {
    const next = applyRuntimeEvent(
      [assistantWithDiscoverySuggestion()],
      event({ payload: { delta: "Planning resumed." } }),
    );

    const assistant = next.find(
      (item): item is Extract<ChatItem, { kind: "message" }> =>
        item.kind === "message" && item.role === "assistant",
    );

    expect(assistant?.content).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          type: "text",
          text: "Planning resumed.",
        }),
      ]),
    );
  });
});
