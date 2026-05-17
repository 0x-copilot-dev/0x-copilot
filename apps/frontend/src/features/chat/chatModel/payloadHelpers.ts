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

// Base set: keys stripped from the *persisted* `argsText` (consumed by
// partFactories / presentation.ts). These are streaming/lifecycle/state
// noise that should never make it into either argsText or the rendered
// "Visible args" JSON block.
//
// `presentation` is the transport-level UI envelope (title, status_label,
// kind, debug_label, …) — it is NOT part of the tool's logical input and
// should never be rendered inside the "Tool details → Input" JSON block.
// It stays on `args` so `presentationFromArgs` can still read it.
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
  "presentation",
  "server_id",
  "server_name",
  "source_tool_call_id",
]);

// Display-only extras: kept in persisted `argsText` (so debug surfaces
// and test fixtures can see which MCP tool an args_delta targeted) but
// hidden from the rendered "Visible args" UI strip.
//
//   - `tool_name` — the called MCP tool name (e.g. "clickup_filter_tasks").
//     MCP relay calls carry it on the args envelope so the user knows
//     which sub-tool was hit; chatModel.test.ts pins that argsText
//     retains it.
//   - `native_interrupt_id` — host-injected interrupt routing id;
//     useful for debugging but visually noisy.
//
// Layered (not duplicated) so the base set above stays the single source
// of truth and an addition to it propagates here automatically.
export const hiddenToolArgKeysForVisibleDisplay = new Set([
  ...hiddenToolArgKeys,
  "native_interrupt_id",
  "tool_name",
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
  "completed_at",
  "display_title",
  "duration_ms",
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
