import type { RuntimeEventEnvelope } from "@enterprise-search/api-types";
import type { MessageStatus as AssistantMessageStatus } from "../runtime/types";
import { isToolCallPart, payloadString } from "./recordHelpers";
import type {
  ChatItem,
  RuntimePartStatus,
  ThreadMessageContent,
} from "./types";

export function isVisibleProgressEvent(event: RuntimeEventEnvelope): boolean {
  return (
    event.event_type === "error" ||
    event.event_type === "run_failed" ||
    event.status === "failed"
  );
}

export function isTerminalRunEvent(event: RuntimeEventEnvelope): boolean {
  return (
    event.event_type === "run_completed" ||
    event.event_type === "run_cancelled" ||
    event.event_type === "run_failed"
  );
}

export function statusForMessage(
  item: Extract<ChatItem, { kind: "message" }>,
  activeRunId: string | null,
): AssistantMessageStatus | undefined {
  if (item.role !== "assistant") {
    return undefined;
  }
  if (
    item.status?.type === "requires-action" ||
    item.status?.type === "incomplete"
  ) {
    return item.status;
  }
  if (activeRunId !== null && item.runId === activeRunId) {
    return { type: "running" };
  }
  return item.status ?? { type: "complete", reason: "stop" };
}

export function nextMessageStatus(
  current: AssistantMessageStatus | undefined,
  next: AssistantMessageStatus,
  content: ThreadMessageContent,
): AssistantMessageStatus {
  if (current?.type === "requires-action" && hasPendingAction(content)) {
    return current;
  }
  return next;
}

export function statusFromRuntimeEvent(
  event: RuntimeEventEnvelope,
): AssistantMessageStatus {
  if (event.event_type === "run_failed" || event.event_type === "error") {
    return { type: "incomplete", reason: "error" };
  }
  if (event.event_type === "run_cancelled") {
    return { type: "incomplete", reason: "cancelled" };
  }
  if (
    event.event_type === "run_completed" ||
    event.event_type === "final_response"
  ) {
    return { type: "complete", reason: "stop" };
  }
  return { type: "running" };
}

export function hasPendingAction(content: ThreadMessageContent): boolean {
  return content.some((part) => {
    if (!isToolCallPart(part)) {
      return false;
    }
    return (
      (part.toolName === "approval_request" ||
        part.toolName === "mcp_auth_required") &&
      part.result === undefined
    );
  });
}

export function statusFromEvent(
  event: RuntimeEventEnvelope,
  fallback: string | null = "unknown",
): RuntimePartStatus {
  const value = (
    event.status ??
    payloadString(event.payload, "status") ??
    fallback ??
    ""
  ).toLowerCase();
  if (value === "queued") {
    return "queued";
  }
  if (value === "cancelled" || event.event_type === "run_cancelled") {
    return "cancelled";
  }
  if (
    value === "failed" ||
    value === "error" ||
    event.event_type === "run_failed" ||
    event.event_type === "error"
  ) {
    return "failed";
  }
  if (
    value === "completed" ||
    value === "succeeded" ||
    value === "success" ||
    event.event_type === "run_completed"
  ) {
    return "completed";
  }
  if (
    value === "waiting" ||
    value === "waiting_for_approval" ||
    event.activity_kind === "approval"
  ) {
    return "waiting";
  }
  if (value === "running" || value === "started" || value === "progress") {
    return "running";
  }
  return "unknown";
}
