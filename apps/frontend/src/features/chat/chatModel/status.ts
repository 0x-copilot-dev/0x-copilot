import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";
import type { MessageStatus as AssistantMessageStatus } from "../runtime/types";
import { asRecord, stringValue } from "../utils/jsonUtils";
import { isToolCallPart, payloadString } from "./recordHelpers";
import type {
  ChatItem,
  RuntimePartStatus,
  ThreadMessageContent,
  ThreadMessageContentPart,
} from "./types";

const RESOLVED_ACTION_STATUSES = new Set([
  "answered",
  "approved",
  "cancelled",
  "forwarded",
  "rejected",
  "skipped",
]);

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
  return content.some(isPendingActionPart);
}

export function hasPendingActionForRun(
  items: readonly ChatItem[],
  runId: string,
): boolean {
  return items.some(
    (item) =>
      item.kind === "message" &&
      item.role === "assistant" &&
      item.runId === runId &&
      hasPendingAction(item.content),
  );
}

export function isPendingActionPart(part: ThreadMessageContentPart): boolean {
  if (
    !isToolCallPart(part) ||
    (part.toolName !== "approval_request" &&
      part.toolName !== "mcp_auth_required") ||
    part.result !== undefined
  ) {
    return false;
  }

  const args = asRecord(part.args);
  const status = stringValue(args.status)?.toLowerCase();
  if (status !== undefined && RESOLVED_ACTION_STATUSES.has(status)) {
    return false;
  }

  // Discovery cards are optional connector suggestions. They can remain
  // unresolved without pausing the run, so they must not suppress planning UI.
  return (
    part.toolName !== "mcp_auth_required" ||
    stringValue(args.discovery_reason) === null
  );
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
