import type { RuntimeEventEnvelope } from "@enterprise-search/api-types";
import { asRecord, stringValue } from "../utils/jsonUtils";
import { activityResultText } from "./largeArtifact";
import {
  argsTextFromRecord,
  hiddenApprovalArgKeys,
  hiddenSubagentArgKeys,
  hiddenToolArgKeys,
  reasoningText,
  toolArgs,
  toolArgsDelta,
  toolCallId,
  toolName,
  toolResultValue,
} from "./payloadHelpers";
import { preferredPresentation, presentationFromValue } from "./presentation";
import {
  jsonArgs,
  payloadString,
  summarizeRecord,
  titleForEvent,
} from "./recordHelpers";
import { statusFromEvent } from "./status";
import {
  meaningfulDisplayText,
  meaningfulSubagentTitle,
  shortSubagentSummary,
  subagentNameForEvent,
} from "./subagentText";
import type { ThreadToolCallPart } from "./types";

export function toolPart(
  event: RuntimeEventEnvelope,
  callId: string,
  existing: ThreadToolCallPart | undefined,
): ThreadToolCallPart {
  const payload = event.payload;
  const existingArgs = asRecord(existing?.args);
  const status = statusFromEvent(event, stringValue(existingArgs.status));
  const args: Record<string, unknown> = {
    ...existingArgs,
    ...toolArgs(payload),
    ...toolArgsDelta(payload),
    status,
  };
  const summary = event.summary ?? payloadString(payload, "summary");
  if (summary) {
    args.summary = summary;
  }
  const presentation = preferredPresentation(
    presentationFromValue(existingArgs.presentation),
    event.presentation ?? null,
  );
  if (presentation) {
    args.presentation = presentation;
  }
  const name =
    toolName(payload) ?? existing?.toolName ?? event.display_title ?? "tool";
  const result = toolResultValue(payload) ?? existing?.result;
  return {
    type: "tool-call",
    toolCallId: callId,
    toolName: name,
    args: jsonArgs(args),
    argsText: argsTextFromRecord(args, hiddenToolArgKeys),
    result,
    isError: status === "failed",
  };
}

export function subagentPart(
  event: RuntimeEventEnvelope,
  key: string,
  existing: ThreadToolCallPart | undefined,
): ThreadToolCallPart | null {
  const existingArgs = asRecord(existing?.args);
  const name =
    subagentNameForEvent(event) ??
    stringValue(existingArgs.subagent_name) ??
    "Subagent";
  const summary =
    event.summary ??
    payloadString(event.payload, "summary") ??
    payloadString(event.payload, "message") ??
    stringValue(existingArgs.summary);
  const shortSummary =
    meaningfulDisplayText(payloadString(event.payload, "short_summary")) ??
    meaningfulDisplayText(stringValue(existingArgs.short_summary)) ??
    shortSubagentSummary(summary);
  const displayTitle =
    meaningfulSubagentTitle(event.display_title) ??
    meaningfulSubagentTitle(payloadString(event.payload, "display_title")) ??
    meaningfulSubagentTitle(stringValue(existingArgs.display_title)) ??
    shortSummary ??
    "Working in the background";
  const existingTaskSummary = stringValue(existingArgs.task_summary);
  const taskSummary =
    shortSummary ??
    existingTaskSummary ??
    (event.event_type === "subagent_completed"
      ? null
      : shortSubagentSummary(summary));
  if (!existing && event.event_type === "subagent_progress" && !summary) {
    return null;
  }
  const status = statusFromEvent(event, stringValue(existingArgs.status));
  const startedAt =
    stringValue(existingArgs.started_at) ??
    (event.event_type === "subagent_started" ? event.created_at : null);
  const args = {
    ...existingArgs,
    subagent_name: name,
    task_id: key,
    display_title: displayTitle,
    status,
    summary: summary ?? null,
    short_summary: shortSummary ?? null,
    task_summary: taskSummary ?? null,
    started_at: startedAt,
  };
  return {
    type: "tool-call",
    toolCallId: key,
    toolName: "run_subagent",
    args: jsonArgs(args),
    argsText: argsTextFromRecord(args, hiddenSubagentArgKeys),
    result: status === "completed" ? summary : existing?.result,
    isError: status === "failed",
  };
}

export function subagentActivityRecord(
  event: RuntimeEventEnvelope,
): Record<string, unknown> {
  if (event.activity_kind === "tool") {
    const callId = toolCallId(event.payload) ?? event.span_id ?? event.event_id;
    const part = toolPart(event, callId, undefined);
    const args = asRecord(part.args);
    return {
      id: callId,
      kind: "tool",
      title: toolName(event.payload) ?? part.toolName,
      status: stringValue(args.status) ?? statusFromEvent(event),
      summary:
        event.summary ??
        payloadString(event.payload, "summary") ??
        stringValue(args.summary),
      input_summary: summarizeRecord(toolArgs(event.payload)),
      result: activityResultText(part.result, part.toolName),
      is_error: part.isError ?? false,
    };
  }
  return {
    id: event.event_id,
    kind: "reasoning",
    title: event.display_title ?? "Reasoning",
    status: statusFromEvent(event),
    summary: reasoningText(event),
    is_error: false,
  };
}

export function progressPart(event: RuntimeEventEnvelope): ThreadToolCallPart {
  const status = statusFromEvent(event);
  const title = event.display_title ?? titleForEvent(event.event_type);
  const summary =
    event.summary ??
    payloadString(event.payload, "message") ??
    payloadString(event.payload, "summary");
  const args = {
    event_type: event.event_type,
    title,
    summary: summary ?? null,
    status,
    presentation: event.presentation ?? null,
  };
  return {
    type: "tool-call",
    toolCallId: event.event_id,
    toolName: "run_progress",
    args: jsonArgs(args),
    argsText: summary ?? title,
    result: status,
    isError: status === "failed",
  };
}

export function approvalPart(
  payload: Record<string, unknown>,
  presentation: RuntimeEventEnvelope["presentation"] = null,
): ThreadToolCallPart {
  const args = jsonArgs({ ...payload, status: "waiting", presentation });
  return {
    type: "tool-call",
    toolCallId: String(payload.approval_id),
    toolName: "approval_request",
    args,
    argsText: argsTextFromRecord(args, hiddenApprovalArgKeys),
  };
}

export function mcpAuthPart(
  payload: Record<string, unknown>,
  presentation: RuntimeEventEnvelope["presentation"] = null,
): ThreadToolCallPart {
  const args = jsonArgs({ ...payload, status: "waiting", presentation });
  const actionId =
    stringValue(payload.approval_id) ??
    stringValue(payload.action_id) ??
    String(payload.server_id);
  return {
    type: "tool-call",
    toolCallId: actionId,
    toolName: "mcp_auth_required",
    args,
    argsText: argsTextFromRecord(args, hiddenToolArgKeys),
  };
}
