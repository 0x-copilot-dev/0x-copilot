// Subagent activity projection — the detailed timeline counterpart to
// `projectSubagents`.
//
// This is a pure selector over the canonical `RuntimeEventEnvelope[]` owned by
// `useRunSession`. It deliberately opens no stream/subscription: callers
// memoize it with the same `session.events` array that drives fleet state,
// tool-call cards, and the ThreadCanvas.
//
// A task's lifecycle events identify the task itself. The *work performed by*
// that task is correlated by `parent_task_id`, which the runtime stamps on
// subagent-scoped tool and reasoning frames. A parent task id alone is not an
// ownership proof: require the runtime's subagent identity (or a subagent-source
// frame) so a main-agent event cannot leak into a child timeline.

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import { stringValue, type SubagentActivityRecord } from "./subagentHelpers";

/** Structurally matches `workspace`'s injected activity-map boundary. */
export type ProjectedSubagentActivitiesByTask = ReadonlyMap<
  string,
  readonly SubagentActivityRecord[]
>;

export interface SubagentActivityProjection {
  readonly activitiesByTask: ProjectedSubagentActivitiesByTask;
}

const EMPTY_ACTIVITIES_BY_TASK: ProjectedSubagentActivitiesByTask = new Map();
const EMPTY_PROJECTION: SubagentActivityProjection = {
  activitiesByTask: EMPTY_ACTIVITIES_BY_TASK,
};

const TOOL_START_EVENTS = new Set([
  "tool_call",
  "tool_call_started",
  "tool_call_delta",
]);
const TOOL_RESULT_EVENTS = new Set(["tool_result", "tool_call_completed"]);
const REASONING_DELTA_EVENT = "reasoning_summary_delta";
const REASONING_SUMMARY_EVENT = "reasoning_summary";
const TOOL_FAILURE_STATUSES = new Set([
  "failed",
  "timed_out",
  "abandoned",
  "cancelled",
  "canceled",
  "error",
]);

type MutableActivity = {
  -readonly [K in keyof SubagentActivityRecord]: SubagentActivityRecord[K];
};

/**
 * Group real inner work by the supervisor task id.
 *
 * Tool lifecycle frames collapse to one row per `(parent_task_id, call_id)` so
 * an expanded timeline naturally changes running → completed/error instead of
 * showing duplicated start/result rows. Reasoning deltas coalesce to the one
 * active summary for that task and its final cap completes that row. The input
 * event list is expected to be sequence-ordered and is never mutated.
 */
export function projectSubagentActivities(
  events: readonly RuntimeEventEnvelope[],
): SubagentActivityProjection {
  if (events.length === 0) {
    return EMPTY_PROJECTION;
  }

  const seenEventIds = new Set<string>();
  const activitiesByTask = new Map<string, MutableActivity[]>();
  const byKey = new Map<string, MutableActivity>();
  const activeReasoningKeyByTask = new Map<string, string>();

  for (const event of events) {
    if (seenEventIds.has(event.event_id)) {
      continue;
    }
    seenEventIds.add(event.event_id);

    const taskId = parentTaskId(event);
    if (taskId === null) {
      continue;
    }

    if (TOOL_START_EVENTS.has(event.event_type)) {
      reduceToolStarted(event, taskId, activitiesByTask, byKey);
      continue;
    }
    if (TOOL_RESULT_EVENTS.has(event.event_type)) {
      reduceToolResult(event, taskId, activitiesByTask, byKey);
      continue;
    }
    if (event.event_type === REASONING_DELTA_EVENT) {
      reduceReasoningDelta(
        event,
        taskId,
        activitiesByTask,
        byKey,
        activeReasoningKeyByTask,
      );
      continue;
    }
    if (event.event_type === REASONING_SUMMARY_EVENT) {
      reduceReasoningSummary(
        event,
        taskId,
        activitiesByTask,
        byKey,
        activeReasoningKeyByTask,
      );
    }
  }

  if (activitiesByTask.size === 0) {
    return EMPTY_PROJECTION;
  }
  return { activitiesByTask };
}

function parentTaskId(event: RuntimeEventEnvelope): string | null {
  const taskId = stringValue(event.parent_task_id);
  if (taskId === null) {
    return null;
  }
  if (stringValue(event.subagent_id) !== null || event.source === "subagent") {
    return taskId;
  }
  return null;
}

function reduceToolStarted(
  event: RuntimeEventEnvelope,
  taskId: string,
  activitiesByTask: Map<string, MutableActivity[]>,
  byKey: Map<string, MutableActivity>,
): void {
  const key = toolKey(taskId, event);
  const existing = byKey.get(key);
  if (existing !== undefined) {
    updateToolActivity(existing, event, false);
    return;
  }
  const activity = toolActivity(event, false);
  addActivity(taskId, key, activity, activitiesByTask, byKey);
}

function reduceToolResult(
  event: RuntimeEventEnvelope,
  taskId: string,
  activitiesByTask: Map<string, MutableActivity[]>,
  byKey: Map<string, MutableActivity>,
): void {
  const key = toolKey(taskId, event);
  const existing = byKey.get(key);
  if (existing !== undefined) {
    updateToolActivity(existing, event, true);
    return;
  }
  const activity = toolActivity(event, true);
  addActivity(taskId, key, activity, activitiesByTask, byKey);
}

function toolKey(taskId: string, event: RuntimeEventEnvelope): string {
  const callId = stringValue(event.payload.call_id) ?? event.event_id;
  return `tool:${taskId}:${callId}`;
}

function toolActivity(
  event: RuntimeEventEnvelope,
  terminal: boolean,
): MutableActivity {
  const status = toolStatus(event, terminal);
  return {
    id: stringValue(event.payload.call_id) ?? event.event_id,
    kind: "tool",
    title: toolName(event),
    status,
    summary: eventSummary(event),
    inputSummary: toolInputSummary(event),
    result: terminal ? toolResultSummary(event) : null,
    isError: isToolError(event, status),
  };
}

function updateToolActivity(
  activity: MutableActivity,
  event: RuntimeEventEnvelope,
  terminal: boolean,
): void {
  const title = toolName(event);
  if (title !== "Tool") {
    activity.title = title;
  }
  const summary = eventSummary(event);
  if (summary !== null) {
    activity.summary = summary;
  }
  const input = toolInputSummary(event);
  if (input !== null) {
    activity.inputSummary = input;
  }
  if (!terminal) {
    const status = toolStatus(event, false);
    if (activity.status === "running" || status !== "running") {
      activity.status = status;
      activity.isError = isToolError(event, status);
    }
    return;
  }
  const status = toolStatus(event, true);
  activity.status = status;
  activity.isError = isToolError(event, status);
  const result = toolResultSummary(event);
  if (result !== null) {
    activity.result = result;
  }
}

function reduceReasoningDelta(
  event: RuntimeEventEnvelope,
  taskId: string,
  activitiesByTask: Map<string, MutableActivity[]>,
  byKey: Map<string, MutableActivity>,
  activeReasoningKeyByTask: Map<string, string>,
): void {
  const delta = eventSummary(event);
  if (delta === null) {
    return;
  }
  const key = activeReasoningKeyByTask.get(taskId) ?? `reasoning:${taskId}`;
  const existing = byKey.get(key);
  if (existing === undefined) {
    addActivity(
      taskId,
      key,
      {
        id: event.event_id,
        kind: "reasoning",
        title: event.display_title ?? "Reasoning",
        status: "running",
        summary: delta,
        inputSummary: null,
        result: null,
        isError: false,
      },
      activitiesByTask,
      byKey,
    );
    activeReasoningKeyByTask.set(taskId, key);
    return;
  }
  existing.summary = appendSummary(existing.summary, delta);
  existing.status = "running";
}

function reduceReasoningSummary(
  event: RuntimeEventEnvelope,
  taskId: string,
  activitiesByTask: Map<string, MutableActivity[]>,
  byKey: Map<string, MutableActivity>,
  activeReasoningKeyByTask: Map<string, string>,
): void {
  const summary = eventSummary(event);
  if (summary === null) {
    return;
  }
  const key =
    activeReasoningKeyByTask.get(taskId) ?? `reasoning:${event.event_id}`;
  const existing = byKey.get(key);
  if (existing === undefined) {
    addActivity(
      taskId,
      key,
      {
        id: event.event_id,
        kind: "reasoning",
        title: event.display_title ?? "Reasoning",
        status: "completed",
        summary,
        inputSummary: null,
        result: null,
        isError: false,
      },
      activitiesByTask,
      byKey,
    );
    return;
  }
  existing.summary = summary;
  existing.status = "completed";
  activeReasoningKeyByTask.delete(taskId);
}

function addActivity(
  taskId: string,
  key: string,
  activity: MutableActivity,
  activitiesByTask: Map<string, MutableActivity[]>,
  byKey: Map<string, MutableActivity>,
): void {
  const activities = activitiesByTask.get(taskId);
  if (activities === undefined) {
    activitiesByTask.set(taskId, [activity]);
  } else {
    activities.push(activity);
  }
  byKey.set(key, activity);
}

function toolName(event: RuntimeEventEnvelope): string {
  return stringValue(event.payload.tool_name) ?? event.display_title ?? "Tool";
}

function toolStatus(event: RuntimeEventEnvelope, terminal: boolean): string {
  const status = stringValue(event.status) ?? stringValue(event.payload.status);
  if (status !== null) {
    return status;
  }
  return terminal ? "completed" : "running";
}

function isToolError(event: RuntimeEventEnvelope, status: string): boolean {
  return (
    TOOL_FAILURE_STATUSES.has(status.toLowerCase()) ||
    stringValue(event.payload.error_message) !== null ||
    stringValue(event.payload.error_code) !== null
  );
}

function eventSummary(event: RuntimeEventEnvelope): string | null {
  return stringValue(event.summary) ?? stringValue(event.payload.summary);
}

function toolInputSummary(event: RuntimeEventEnvelope): string | null {
  const args = event.payload.args;
  if (args === undefined || args === null) {
    return null;
  }
  if (typeof args === "string") {
    return stringValue(args);
  }
  if (typeof args !== "object" || Array.isArray(args)) {
    return null;
  }
  try {
    const serialized = JSON.stringify(args);
    return serialized === "{}" ? null : serialized;
  } catch {
    return null;
  }
}

function toolResultSummary(event: RuntimeEventEnvelope): string | null {
  return (
    eventSummary(event) ??
    stringValue(event.payload.safe_message) ??
    stringValue(event.payload.error_message) ??
    serialiseOutput(event.payload.output)
  );
}

function serialiseOutput(value: unknown): string | null {
  if (value === undefined || value === null) {
    return null;
  }
  if (typeof value === "string") {
    return stringValue(value);
  }
  if (typeof value !== "object") {
    return String(value);
  }
  try {
    const serialized = JSON.stringify(value);
    return serialized === "{}" ? null : serialized;
  } catch {
    return null;
  }
}

function appendSummary(current: string | null, delta: string): string {
  return current === null ? delta : `${current}${delta}`;
}
