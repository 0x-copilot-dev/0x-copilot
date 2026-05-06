import type { RuntimeEventEnvelope } from "@enterprise-search/api-types";
import { describe, expect, it } from "vitest";
import type { ChatItem } from "./chatModel";
import { deriveRunUiState, isRunUiEvent } from "./chatRunState";

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

function pendingApprovalItem(runId = "run_123"): ChatItem {
  return {
    id: `assistant-${runId}`,
    kind: "message",
    role: "assistant",
    runId,
    content: [
      {
        type: "tool-call",
        toolCallId: "approval_123",
        toolName: "approval_request",
        args: { approval_id: "approval_123" },
      },
    ],
  } as ChatItem;
}

function pendingMcpDiscoveryItem(runId = "run_123"): ChatItem {
  return {
    id: `assistant-${runId}`,
    kind: "message",
    role: "assistant",
    runId,
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

function resolvedApprovalItemWithoutResult(runId = "run_123"): ChatItem {
  return {
    id: `assistant-${runId}`,
    kind: "message",
    role: "assistant",
    runId,
    content: [
      {
        type: "tool-call",
        toolCallId: "approval_123",
        toolName: "approval_request",
        args: { approval_id: "approval_123", status: "answered" },
      },
    ],
  } as ChatItem;
}

describe("deriveRunUiState", () => {
  it("shows planning while an active run is starting", () => {
    const state = deriveRunUiState({
      activeRunId: "run_123",
      items: [],
      latestEvent: null,
    });

    expect(state).toMatchObject({
      phase: "starting",
      headerStatus: "Working...",
      showPlanningIndicator: true,
    });
  });

  it("suppresses planning while waiting for user permission", () => {
    const state = deriveRunUiState({
      activeRunId: "run_123",
      items: [pendingApprovalItem()],
      latestEvent: event({
        event_type: "tool_call_completed",
        activity_kind: "tool",
        status: "completed",
        payload: { status: "completed" },
      }),
    });

    expect(state).toMatchObject({
      phase: "waiting_for_permission",
      headerStatus: "Waiting for permission...",
      showPlanningIndicator: false,
    });
  });

  it("keeps planning visible for unresolved optional MCP discovery suggestions", () => {
    const state = deriveRunUiState({
      activeRunId: "run_123",
      items: [pendingMcpDiscoveryItem()],
      latestEvent: event({
        event_type: "tool_call_completed",
        activity_kind: "tool",
        status: "completed",
        payload: { status: "completed" },
      }),
    });

    expect(state).toMatchObject({
      phase: "working",
      headerStatus: "Working...",
      showPlanningIndicator: true,
    });
  });

  it("keeps planning visible when an action has a resolved status but no result", () => {
    const state = deriveRunUiState({
      activeRunId: "run_123",
      items: [resolvedApprovalItemWithoutResult()],
      latestEvent: event({
        event_type: "tool_call_completed",
        activity_kind: "tool",
        status: "completed",
        payload: { status: "completed" },
      }),
    });

    expect(state).toMatchObject({
      phase: "working",
      headerStatus: "Working...",
      showPlanningIndicator: true,
    });
  });

  it("lets terminal events win over pending user permission", () => {
    const state = deriveRunUiState({
      activeRunId: "run_123",
      items: [pendingApprovalItem()],
      latestEvent: event({
        event_type: "run_failed",
        activity_kind: "run",
        status: "failed",
        payload: { status: "failed" },
      }),
    });

    expect(state).toMatchObject({
      phase: "terminal",
      headerStatus: "Could not complete",
      showPlanningIndicator: false,
    });
  });

  it("hides planning while visible assistant text is streaming", () => {
    const state = deriveRunUiState({
      activeRunId: "run_123",
      items: [],
      latestEvent: event({
        event_type: "model_delta",
        activity_kind: "message",
        payload: { delta: "Here is the answer" },
      }),
    });

    expect(state).toMatchObject({
      phase: "writing",
      headerStatus: "Writing answer...",
      showPlanningIndicator: false,
    });
  });

  it("shows planning after a completed action returns control to the agent", () => {
    const state = deriveRunUiState({
      activeRunId: "run_123",
      items: [],
      latestEvent: event({
        event_type: "tool_call_completed",
        activity_kind: "tool",
        status: "completed",
        payload: { status: "completed" },
      }),
    });

    expect(state).toMatchObject({
      phase: "working",
      headerStatus: "Working...",
      showPlanningIndicator: true,
    });
  });

  it("shows planning after a tool result even if the envelope status is stale", () => {
    const state = deriveRunUiState({
      activeRunId: "run_123",
      items: [],
      latestEvent: event({
        event_type: "tool_result",
        activity_kind: "tool",
        status: "running",
        payload: { output: { ok: true } },
      }),
    });

    expect(state).toMatchObject({
      phase: "working",
      headerStatus: "Working...",
      showPlanningIndicator: true,
    });
  });

  it("classifies action completion by event type before generic status and kind", () => {
    const state = deriveRunUiState({
      activeRunId: "run_123",
      items: [],
      latestEvent: event({
        event_type: "tool_call_completed",
        activity_kind: "event",
        status: "running",
        payload: { status: "running" },
      }),
    });

    expect(state).toMatchObject({
      phase: "working",
      showPlanningIndicator: true,
    });
  });

  it("classifies action start by event type before generic kind", () => {
    const state = deriveRunUiState({
      activeRunId: "run_123",
      items: [],
      latestEvent: event({
        event_type: "tool_call_started",
        activity_kind: "event",
        status: "queued",
        payload: { status: "queued" },
      }),
    });

    expect(state).toMatchObject({
      phase: "acting",
      showPlanningIndicator: false,
    });
  });

  it("does not show planning while an action is still running", () => {
    const state = deriveRunUiState({
      activeRunId: "run_123",
      items: [],
      latestEvent: event({
        event_type: "tool_call_started",
        activity_kind: "tool",
        status: "running",
        payload: { status: "running" },
      }),
    });

    expect(state).toMatchObject({
      phase: "acting",
      headerStatus: "Running action...",
      showPlanningIndicator: false,
    });
  });

  it("returns to planning after subagent completion", () => {
    const state = deriveRunUiState({
      activeRunId: "run_123",
      items: [],
      latestEvent: event({
        event_type: "subagent_completed",
        activity_kind: "subagent",
        status: "running",
        payload: { status: "running" },
      }),
    });

    expect(state).toMatchObject({
      phase: "working",
      headerStatus: "Working...",
      showPlanningIndicator: true,
    });
  });

  it("hides planning for terminal run events", () => {
    const state = deriveRunUiState({
      activeRunId: "run_123",
      items: [],
      latestEvent: event({
        event_type: "run_completed",
        activity_kind: "run",
        status: "completed",
      }),
    });

    expect(state).toMatchObject({
      phase: "terminal",
      headerStatus: "Ready",
      showPlanningIndicator: false,
    });
  });

  it("ignores stale events from another active run", () => {
    const state = deriveRunUiState({
      activeRunId: "run_456",
      items: [],
      latestEvent: event({
        run_id: "run_123",
        event_type: "model_delta",
        activity_kind: "message",
        payload: { delta: "Stale text" },
      }),
    });

    expect(state.phase).toBe("starting");
    expect(state.showPlanningIndicator).toBe(true);
  });
});

describe("isRunUiEvent", () => {
  it("filters internal and heartbeat events out of the run UI model", () => {
    expect(
      isRunUiEvent(
        event({
          visibility: "internal",
          event_type: "model_delta",
          payload: { delta: "hidden" },
        }),
      ),
    ).toBe(false);
    expect(isRunUiEvent(event({ activity_kind: "heartbeat" }))).toBe(false);
    expect(isRunUiEvent(event({ event_type: "run_started" }))).toBe(true);
  });
});
