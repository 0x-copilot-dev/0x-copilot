import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";
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
  // Plumb the backend-projected display title through so renderers can
  // surface it verbatim (`apps/frontend/CLAUDE.md`: "Use the backend's
  // projected display_title / summary / status fields. Do not derive
  // activity types from event-name prefixes on the client."). The
  // projector unwraps the MCP dispatcher (e.g. ``"Calling list_issues"``),
  // so the renderer never has to recompute the title from raw args.
  const displayTitle =
    event.display_title ?? stringValue(existingArgs.display_title);
  if (displayTitle) {
    args.display_title = displayTitle;
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
  // PR A2 — capture `parent_fleet_id` so the renderer can decide whether to
  // suppress this subagent card in favor of nesting under a `<SubagentFleetCard>`.
  // Always preserves the original (server-stamped) value once seen so a late
  // PROGRESS / COMPLETED without the field can't blank it out.
  const parentFleetId =
    payloadString(event.payload, "parent_fleet_id") ??
    stringValue(existingArgs.parent_fleet_id);
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
  const completedAt =
    stringValue(existingArgs.completed_at) ??
    (event.event_type === "subagent_completed" ? event.created_at : null);
  const durationMs =
    numberValue(existingArgs.duration_ms) ??
    payloadNumber(event.payload, "duration_ms") ??
    durationFromStarted(startedAt, completedAt);
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
    completed_at: completedAt,
    duration_ms: durationMs,
    parent_fleet_id: parentFleetId ?? null,
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

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function payloadNumber(payload: unknown, key: string): number | null {
  const data = asRecord(payload);
  return numberValue(data[key]);
}

function durationFromStarted(
  startedAt: string | null,
  completedAt: string | null,
): number | null {
  if (startedAt === null || completedAt === null) {
    return null;
  }
  const started = Date.parse(startedAt);
  const completed = Date.parse(completedAt);
  if (Number.isNaN(started) || Number.isNaN(completed)) {
    return null;
  }
  return Math.max(0, completed - started);
}

// PR A2 — fleet bookend part. Emitted on `subagent_fleet_started` and
// updated on each child `subagent_*` event (running/done counts) plus
// `subagent_fleet_finished` (elapsed). Renders as `<SubagentFleetCard>`
// via the `run_subagent_fleet` tool-name route.
export function subagentFleetPart(
  event: RuntimeEventEnvelope,
  fleetId: string,
  existing: ThreadToolCallPart | undefined,
): ThreadToolCallPart {
  const existingArgs = asRecord(existing?.args);
  const payload =
    event.payload && typeof event.payload === "object"
      ? (event.payload as Record<string, unknown>)
      : {};
  const title =
    payloadString(payload, "title") ??
    stringValue(existingArgs.title) ??
    event.display_title ??
    "Subagents working in parallel";
  const sub =
    payloadString(payload, "sub") ?? stringValue(existingArgs.sub) ?? null;
  const agentIds =
    readStringArray(payload.agent_ids) ??
    readStringArray(existingArgs.agent_ids) ??
    [];
  const total = agentIds.length;
  const elapsed =
    payloadString(payload, "elapsed") ??
    stringValue(existingArgs.elapsed) ??
    null;
  const completed =
    event.event_type === "subagent_fleet_finished" ||
    existingArgs.completed === true;
  const args = {
    ...existingArgs,
    fleet_id: fleetId,
    title,
    sub,
    agent_ids: agentIds,
    total,
    elapsed,
    completed,
  };
  return {
    type: "tool-call",
    toolCallId: fleetId,
    toolName: "run_subagent_fleet",
    args: jsonArgs(args),
    argsText: title,
    result: completed ? "completed" : undefined,
    isError: false,
  };
}

function readStringArray(value: unknown): string[] | null {
  if (!Array.isArray(value)) {
    return null;
  }
  const out: string[] = [];
  for (const item of value) {
    if (typeof item === "string" && item.trim().length > 0) {
      out.push(item);
    }
  }
  return out;
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
