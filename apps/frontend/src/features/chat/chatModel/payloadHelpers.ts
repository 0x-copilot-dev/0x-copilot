import {
  isReasoningSummaryDeltaPayload,
  isReasoningSummaryPayload,
  isToolCallDeltaPayload,
  isToolCallPayload,
  isToolResultPayload,
  type RuntimeEventEnvelope,
} from "@enterprise-search/api-types";
import { isPlainRecord, objectSummary, payloadString } from "./recordHelpers";

export function reasoningText(event: RuntimeEventEnvelope): string | null {
  if (isReasoningSummaryDeltaPayload(event.payload)) {
    return event.payload.delta;
  }
  if (isReasoningSummaryPayload(event.payload)) {
    return event.payload.summary;
  }
  return event.summary ?? null;
}

export function toolName(payload: Record<string, unknown>): string | null {
  if (isToolCallPayload(payload) || isToolResultPayload(payload)) {
    return payload.tool_name;
  }
  return payloadString(payload, "tool_name");
}

export function toolCallId(payload: Record<string, unknown>): string | null {
  if (
    isToolCallPayload(payload) ||
    isToolCallDeltaPayload(payload) ||
    isToolResultPayload(payload)
  ) {
    return payload.call_id;
  }
  return payloadString(payload, "call_id");
}

export function toolArgs(
  payload: Record<string, unknown>,
): Record<string, unknown> {
  if (isToolCallPayload(payload) && isPlainRecord(payload.args)) {
    return payload.args;
  }
  return {};
}

export function toolArgsDelta(
  payload: Record<string, unknown>,
): Record<string, unknown> {
  if (isToolCallDeltaPayload(payload) && isPlainRecord(payload.args_delta)) {
    return payload.args_delta;
  }
  return {};
}

export function toolResultValue(payload: Record<string, unknown>): unknown {
  if (!isToolResultPayload(payload)) {
    return payloadString(payload, "summary") ?? undefined;
  }
  if (payload.output && Object.keys(payload.output).length > 0) {
    return payload.output;
  }
  return (
    payload.summary ?? payload.safe_message ?? objectSummary(payload.output)
  );
}

export const hiddenToolArgKeys = new Set([
  "status",
  "summary",
  "delta",
  "deltas",
  "event_type",
  "action_id",
  "approval_id",
  "approval_kind",
  "auth_url",
  "display_name",
  "server_id",
  "server_name",
  "source_tool_call_id",
]);

export const hiddenApprovalArgKeys = new Set([
  ...hiddenToolArgKeys,
  "api_event_type",
  "approval_id",
  "approval_kind",
  "event",
  "grant_options",
  "kind",
  "source_tool_call_id",
]);

export const hiddenSubagentArgKeys = new Set([
  ...hiddenToolArgKeys,
  "activities",
  "display_title",
  "short_summary",
  "started_at",
  "subagent_name",
  "task_id",
  "task_summary",
]);

export function argsTextFromRecord(
  args: Record<string, unknown>,
  hiddenKeys: ReadonlySet<string>,
): string | undefined {
  const visible: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(args)) {
    if (hiddenKeys.has(key) || value === undefined || value === null) {
      continue;
    }
    visible[key] = value;
  }
  return Object.keys(visible).length > 0
    ? JSON.stringify(visible, null, 2)
    : undefined;
}
