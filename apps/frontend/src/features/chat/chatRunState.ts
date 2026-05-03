import type { RuntimeEventEnvelope } from "@enterprise-search/api-types";
import type { ChatItem } from "./chatModel";

export type RunUiPhase =
  | "idle"
  | "starting"
  | "working"
  | "acting"
  | "writing"
  | "reasoning"
  | "waiting_for_permission"
  | "terminal";

export type RunUiState = {
  phase: RunUiPhase;
  headerStatus: string;
  showPlanningIndicator: boolean;
  planningLabel: string;
};

export function deriveRunUiState({
  activeRunId,
  items,
  latestEvent,
}: {
  activeRunId: string | null;
  items: readonly ChatItem[];
  latestEvent: RuntimeEventEnvelope | null;
}): RunUiState {
  const planningLabel = "Planning next step...";
  if (activeRunId === null) {
    return {
      phase: "idle",
      headerStatus: "Ready",
      showPlanningIndicator: false,
      planningLabel,
    };
  }

  const event = latestEvent?.run_id === activeRunId ? latestEvent : null;
  const eventPhase = phaseForEvent(event);
  if (eventPhase !== "terminal" && runHasPendingAction(items, activeRunId)) {
    return {
      phase: "waiting_for_permission",
      headerStatus: "Waiting for permission...",
      showPlanningIndicator: false,
      planningLabel,
    };
  }

  return {
    phase: eventPhase,
    headerStatus: headerStatusForPhase(eventPhase, event),
    showPlanningIndicator:
      eventPhase === "starting" || eventPhase === "working",
    planningLabel,
  };
}

export function isRunUiEvent(event: RuntimeEventEnvelope): boolean {
  return event.visibility !== "internal" && event.activity_kind !== "heartbeat";
}

function phaseForEvent(event: RuntimeEventEnvelope | null): RunUiPhase {
  if (event === null) {
    return "starting";
  }
  if (
    event.event_type === "run_completed" ||
    event.event_type === "run_cancelled" ||
    event.event_type === "run_failed"
  ) {
    return "terminal";
  }
  if (
    event.event_type === "model_delta" ||
    event.event_type === "final_response"
  ) {
    return visibleAssistantTextEvent(event) ? "writing" : "working";
  }
  if (
    event.event_type === "reasoning_summary" ||
    event.event_type === "reasoning_summary_delta"
  ) {
    return "reasoning";
  }
  if (isCompletedActionEvent(event)) {
    return "working";
  }
  if (isStartedActionEvent(event)) {
    return "acting";
  }
  if (
    (event.activity_kind === "tool" || event.activity_kind === "subagent") &&
    eventStatusIsActive(event)
  ) {
    return "acting";
  }
  return event.event_type === "run_started" ? "starting" : "working";
}

function headerStatusForPhase(
  phase: RunUiPhase,
  event: RuntimeEventEnvelope | null,
): string {
  switch (phase) {
    case "idle":
      return "Ready";
    case "starting":
    case "working":
      return "Working...";
    case "acting":
      return "Running action...";
    case "writing":
      return "Writing answer...";
    case "reasoning":
      return "Thinking...";
    case "waiting_for_permission":
      return "Waiting for permission...";
    case "terminal":
      if (event?.event_type === "run_completed") {
        return "Ready";
      }
      if (event?.event_type === "run_failed") {
        return "Could not complete";
      }
      return "Stopped";
  }
}

function runHasPendingAction(
  items: readonly ChatItem[],
  runId: string,
): boolean {
  return items.some(
    (item) =>
      item.kind === "message" &&
      item.role === "assistant" &&
      item.runId === runId &&
      item.content.some(
        (part) =>
          part.type === "tool-call" &&
          (part.toolName === "approval_request" ||
            part.toolName === "mcp_auth_required") &&
          part.result === undefined,
      ),
  );
}

function isCompletedActionEvent(event: RuntimeEventEnvelope): boolean {
  return (
    event.event_type === "tool_call_completed" ||
    event.event_type === "tool_result" ||
    event.event_type === "subagent_completed"
  );
}

function isStartedActionEvent(event: RuntimeEventEnvelope): boolean {
  return (
    event.event_type === "tool_call_started" ||
    event.event_type === "tool_call_delta" ||
    event.event_type === "subagent_started" ||
    event.event_type === "subagent_progress"
  );
}

function eventStatusIsActive(event: RuntimeEventEnvelope): boolean {
  const status = eventStatus(event);
  return (
    status === "running" ||
    status === "queued" ||
    status === "starting" ||
    status === "waiting"
  );
}

function eventStatus(event: RuntimeEventEnvelope): string | null {
  const payload =
    event.payload && typeof event.payload === "object"
      ? (event.payload as Record<string, unknown>)
      : {};
  const status = payload.status ?? event.status;
  return typeof status === "string" ? status.toLowerCase() : null;
}

function visibleAssistantTextEvent(event: RuntimeEventEnvelope): boolean {
  const payload =
    event.payload && typeof event.payload === "object"
      ? (event.payload as Record<string, unknown>)
      : {};
  const text =
    stringFromPayloadField(payload, "delta") ??
    stringFromPayloadField(payload, "message") ??
    stringFromPayloadField(payload, "summary");
  if (!text?.trim()) {
    return false;
  }
  return !/^checkpoint\s*:/i.test(text.trimStart());
}

function stringFromPayloadField(
  payload: Record<string, unknown>,
  field: string,
): string | null {
  const value = payload[field];
  return typeof value === "string" ? value : null;
}
