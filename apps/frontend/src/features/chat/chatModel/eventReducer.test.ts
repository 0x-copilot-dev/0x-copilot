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

  // PR 4.4.7 Phase 2 (Slice C) — catalog-suggestion payloads omit
  // ``auth_url`` / ``expires_at`` (the runtime projector strips empty
  // strings, and there's no MCP server row yet to issue an auth URL
  // against). Before this fix, ``isMcpAuthRequiredPayload`` rejected
  // the payload as malformed and the reducer dropped the event,
  // leaving the user with the agent's "tap Connect above" text but no
  // card to tap.
  it("projects a catalog-suggestion mcp_auth_required payload without auth_url into a card", () => {
    const assistantSeed = {
      id: "assistant-run_xyz",
      kind: "message",
      role: "assistant",
      runId: "run_xyz",
      content: [],
    } as ChatItem;

    const next = applyRuntimeEvent(
      [assistantSeed],
      event({
        run_id: "run_xyz",
        event_id: "event_disc_1",
        event_type: "mcp_auth_required",
        activity_kind: "mcp_auth",
        status: "waiting",
        payload: {
          // Note: NO auth_url, NO expires_at — exactly what the catalog
          // path emits.
          approval_id: "mcp_discovery:run_xyz:linear",
          action_id: "mcp_discovery:run_xyz:linear",
          approval_kind: "mcp_auth",
          server_id: "seed:linear",
          server_name: "linear",
          display_name: "Linear",
          message: "Linear isn't connected.",
          discovery_reason: "fetch ticket statuses",
          expected_value: "ground claims about ticket progress",
          catalog_slug: "linear",
        },
      }),
    );

    const assistant = next.find(
      (item): item is Extract<ChatItem, { kind: "message" }> =>
        item.kind === "message" && item.role === "assistant",
    );
    const part = assistant?.content.find(
      (entry) =>
        entry.type === "tool-call" && entry.toolName === "mcp_auth_required",
    );
    expect(
      part,
      "discovery card was projected into the assistant content",
    ).toBeDefined();
  });
});
