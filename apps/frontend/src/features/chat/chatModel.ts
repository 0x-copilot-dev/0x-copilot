import type {
  ApprovalRequestedPayload,
  McpAuthRequiredEventPayload,
  Message,
  RuntimeEventEnvelope
} from "@enterprise-search/api-types";
import {
  isApprovalRequestedPayload,
  isReasoningSummaryDeltaPayload,
  isReasoningSummaryPayload,
  isRuntimeTextPayload,
  isSubagentActivityPayload,
  isToolCallDeltaPayload,
  isToolCallPayload,
  isToolResultPayload
} from "@enterprise-search/api-types";
import { isMcpAuthRequiredPayload } from "../../api/mcpApi";

export type ActivityStatus = "queued" | "running" | "completed" | "failed" | "cancelled" | "waiting" | "unknown";

export interface ActivityText {
  id: string;
  text: string;
}

export interface ActivityEvent {
  id: string;
  eventType: string;
  title: string;
  summary?: string;
  status: ActivityStatus;
}

export interface ToolCallActivity {
  id: string;
  name: string;
  status: ActivityStatus;
  summary?: string;
  result?: string;
  deltas: ActivityText[];
}

export interface SubagentActivity {
  id: string;
  taskId: string;
  name: string;
  status: ActivityStatus;
  summary?: string;
  reasoning: ActivityText[];
  events: ActivityEvent[];
  tools: ToolCallActivity[];
}

export interface RunActivity {
  runId: string;
  status: ActivityStatus;
  title: string;
  summary?: string;
  reasoning: ActivityText[];
  events: ActivityEvent[];
  tools: ToolCallActivity[];
  subagents: SubagentActivity[];
}

export type ChatItem =
  | {
      id: string;
      kind: "message";
      role: "user" | "assistant" | "system";
      text: string;
    }
  | {
      id: string;
      kind: "run-activity";
      activity: RunActivity;
    }
  | {
      id: string;
      kind: "status";
      title: string;
      text?: string;
    }
  | {
      id: string;
      kind: "mcp-auth";
      payload: McpAuthRequiredEventPayload;
    }
  | {
      id: string;
      kind: "approval";
      payload: ApprovalRequestedPayload;
    };

export function messagesToChatItems(messages: Message[]): ChatItem[] {
  return messages
    .filter((message) => message.status !== "deleted")
    .map((message) => ({
      id: message.message_id,
      kind: "message",
      role: message.role === "assistant" || message.role === "user" ? message.role : "system",
      text: message.content_text
    }));
}

export function optimisticUserMessage(text: string): ChatItem {
  return {
    id: `local-${Date.now()}`,
    kind: "message",
    role: "user",
    text
  };
}

export function applyRuntimeEvent(items: ChatItem[], event: RuntimeEventEnvelope): ChatItem[] {
  if (event.event_type === "heartbeat") {
    return items;
  }
  if (event.event_type === "mcp_auth_required" && isMcpAuthRequiredPayload(event.payload)) {
    return upsertById(items, {
      id: event.event_id,
      kind: "mcp-auth",
      payload: event.payload
    });
  }
  if (event.event_type === "approval_requested" && isApprovalRequestedPayload(event.payload)) {
    return upsertById(items, {
      id: event.event_id,
      kind: "approval",
      payload: event.payload
    });
  }
  if (event.event_type === "model_delta" && isRuntimeTextPayload(event.payload)) {
    const delta = textFromPayload(event.payload, "delta");
    if (!delta) {
      return items;
    }
    return appendAssistantDelta(items, event.run_id, delta);
  }
  if (event.event_type === "final_response" && isRuntimeTextPayload(event.payload)) {
    const withActivity = upsertRunActivity(items, event);
    const text = textFromPayload(event.payload, "message") ?? textFromPayload(event.payload, "summary");
    if (!text) {
      return withActivity;
    }
    return finalizeAssistantMessage(withActivity, event.run_id, text);
  }
  if (isActivityEvent(event)) {
    return upsertRunActivity(items, event);
  }
  if (isRuntimeTextPayload(event.payload)) {
    const title = event.display_title ?? titleForEvent(event.event_type);
    const text = textFromPayload(event.payload, "message") ?? event.summary ?? undefined;
    return upsertById(items, {
      id: event.event_id,
      kind: "status",
      title,
      text
    });
  }
  return items;
}

function isActivityEvent(event: RuntimeEventEnvelope): boolean {
  return (
    event.event_type.startsWith("run_") ||
    event.event_type === "progress" ||
    event.event_type === "reasoning_summary" ||
    event.event_type === "reasoning_summary_delta" ||
    event.event_type === "tool_call" ||
    event.event_type === "tool_call_started" ||
    event.event_type === "tool_call_delta" ||
    event.event_type === "tool_result" ||
    event.event_type === "tool_call_completed" ||
    event.event_type === "subagent_update" ||
    event.event_type === "subagent_started" ||
    event.event_type === "subagent_progress" ||
    event.event_type === "subagent_completed" ||
    event.event_type === "observation" ||
    event.event_type === "error"
  );
}

function upsertRunActivity(items: ChatItem[], event: RuntimeEventEnvelope): ChatItem[] {
  const id = `activity-${event.run_id}`;
  const existing = items.find((item): item is Extract<ChatItem, { kind: "run-activity" }> => {
    return item.kind === "run-activity" && item.id === id;
  });
  const activity = applyActivityEvent(existing?.activity ?? createRunActivity(event), event);
  if (!existing) {
    return [...items, { id, kind: "run-activity", activity }];
  }
  return items.map((item) => (item.id === id && item.kind === "run-activity" ? { ...item, activity } : item));
}

function createRunActivity(event: RuntimeEventEnvelope): RunActivity {
  return {
    runId: event.run_id,
    status: statusFromEvent(event),
    title: event.display_title ?? "Agent activity",
    summary: event.summary ?? undefined,
    reasoning: [],
    events: [],
    tools: [],
    subagents: []
  };
}

function applyActivityEvent(activity: RunActivity, event: RuntimeEventEnvelope): RunActivity {
  let next: RunActivity = {
    ...activity,
    status: statusFromEvent(event, activity.status),
    title: runTitleForEvent(event, activity),
    summary: event.summary ?? activity.summary,
    reasoning: [...activity.reasoning],
    events: [...activity.events],
    tools: [...activity.tools],
    subagents: activity.subagents.map((subagent) => ({
      ...subagent,
      reasoning: [...subagent.reasoning],
      events: [...subagent.events],
      tools: subagent.tools.map((tool) => ({ ...tool, deltas: [...tool.deltas] }))
    }))
  };

  if (event.event_type === "reasoning_summary" || event.event_type === "reasoning_summary_delta") {
    return appendReasoning(next, event);
  }
  if (event.event_type.startsWith("tool_")) {
    return upsertTool(next, event);
  }
  if (event.event_type.startsWith("subagent_")) {
    return upsertSubagent(next, event);
  }
  if (event.event_type !== "model_delta" && event.event_type !== "final_response") {
    next = appendActivityRow(next, event);
  }
  return next;
}

function appendReasoning(activity: RunActivity, event: RuntimeEventEnvelope): RunActivity {
  const text = reasoningText(event);
  if (!text) {
    return activity;
  }
  const subagentKey = subagentKeyForEvent(event);
  if (subagentKey) {
    const withSubagent = ensureSubagent(activity, event, subagentKey);
    return {
      ...withSubagent,
      subagents: withSubagent.subagents.map((subagent) =>
        subagent.id === subagentKey
          ? { ...subagent, reasoning: upsertText(subagent.reasoning, event.event_id, text) }
          : subagent
      )
    };
  }
  return { ...activity, reasoning: upsertText(activity.reasoning, event.event_id, text) };
}

function upsertTool(activity: RunActivity, event: RuntimeEventEnvelope): RunActivity {
  const tool = toolFromEvent(event);
  const subagentKey = subagentKeyForEvent(event);
  if (subagentKey) {
    const withSubagent = ensureSubagent(activity, event, subagentKey);
    return {
      ...withSubagent,
      subagents: withSubagent.subagents.map((subagent) =>
        subagent.id === subagentKey ? { ...subagent, tools: upsertToolInList(subagent.tools, tool, event) } : subagent
      )
    };
  }
  return { ...activity, tools: upsertToolInList(activity.tools, tool, event) };
}

function upsertSubagent(activity: RunActivity, event: RuntimeEventEnvelope): RunActivity {
  const key = subagentKeyForEvent(event) ?? event.event_id;
  const withSubagent = ensureSubagent(activity, event, key);
  return {
    ...withSubagent,
    subagents: withSubagent.subagents.map((subagent) => {
      if (subagent.id !== key) {
        return subagent;
      }
      return {
        ...subagent,
        status: statusFromEvent(event, subagent.status),
        summary: event.summary ?? payloadString(event.payload, "summary") ?? subagent.summary,
        events: upsertEvent(subagent.events, event)
      };
    })
  };
}

function appendActivityRow(activity: RunActivity, event: RuntimeEventEnvelope): RunActivity {
  const subagentKey = subagentKeyForEvent(event);
  if (subagentKey) {
    const withSubagent = ensureSubagent(activity, event, subagentKey);
    return {
      ...withSubagent,
      subagents: withSubagent.subagents.map((subagent) =>
        subagent.id === subagentKey ? { ...subagent, events: upsertEvent(subagent.events, event) } : subagent
      )
    };
  }
  return { ...activity, events: upsertEvent(activity.events, event) };
}

function ensureSubagent(activity: RunActivity, event: RuntimeEventEnvelope, key: string): RunActivity {
  if (activity.subagents.some((subagent) => subagent.id === key)) {
    return activity;
  }
  const payloadName = isSubagentActivityPayload(event.payload)
    ? event.payload.subagent_name ?? event.payload.subagent_id
    : undefined;
  const name = event.subagent_id ?? payloadName ?? "subagent";
  return {
    ...activity,
    subagents: [
      ...activity.subagents,
      {
        id: key,
        taskId: event.task_id ?? (isSubagentActivityPayload(event.payload) ? event.payload.task_id : key),
        name,
        status: statusFromEvent(event),
        summary: event.summary ?? payloadString(event.payload, "summary") ?? undefined,
        reasoning: [],
        events: [],
        tools: []
      }
    ]
  };
}

function upsertToolInList(tools: ToolCallActivity[], tool: ToolCallActivity, event: RuntimeEventEnvelope): ToolCallActivity[] {
  const existing = tools.find((item) => item.id === tool.id);
  if (!existing) {
    return [...tools, tool];
  }
  return tools.map((item) => {
    if (item.id !== tool.id) {
      return item;
    }
    return {
      ...item,
      name: tool.name,
      status: tool.status,
      summary: tool.summary ?? item.summary,
      result: tool.result ?? item.result,
      deltas: mergeText(item.deltas, tool.deltas)
    };
  });
}

function toolFromEvent(event: RuntimeEventEnvelope): ToolCallActivity {
  const payload = event.payload;
  const fallbackId = event.span_id ?? event.event_id;
  const name = toolName(payload) ?? event.display_title ?? "tool";
  const deltas: ActivityText[] =
    isToolCallDeltaPayload(payload) && typeof payload.delta === "string"
      ? [{ id: event.event_id, text: payload.delta }]
      : [];
  return {
    id: toolCallId(payload) ?? fallbackId,
    name,
    status: statusFromEvent(event),
    summary: event.summary ?? payloadString(payload, "summary") ?? undefined,
    result: toolResultText(payload),
    deltas
  };
}

function toolName(payload: Record<string, unknown>): string | null {
  if (isToolCallPayload(payload) || isToolResultPayload(payload)) {
    return payload.tool_name;
  }
  const value = payload.tool_name;
  return typeof value === "string" && value.trim() ? value : null;
}

function toolCallId(payload: Record<string, unknown>): string | null {
  if (isToolCallPayload(payload) || isToolCallDeltaPayload(payload) || isToolResultPayload(payload)) {
    return payload.call_id;
  }
  const value = payload.call_id;
  return typeof value === "string" && value.trim() ? value : null;
}

function toolResultText(payload: Record<string, unknown>): string | undefined {
  if (!isToolResultPayload(payload)) {
    return payloadString(payload, "summary") ?? undefined;
  }
  return payload.summary ?? payload.safe_message ?? objectSummary(payload.output);
}

function reasoningText(event: RuntimeEventEnvelope): string | null {
  if (isReasoningSummaryDeltaPayload(event.payload)) {
    return event.payload.delta;
  }
  if (isReasoningSummaryPayload(event.payload)) {
    return event.payload.summary;
  }
  return event.summary ?? null;
}

function subagentKeyForEvent(event: RuntimeEventEnvelope): string | null {
  return event.task_id ?? event.parent_task_id ?? event.subagent_id ?? null;
}

function upsertEvent(events: ActivityEvent[], event: RuntimeEventEnvelope): ActivityEvent[] {
  return upsertByKey(events, event.event_id, {
    id: event.event_id,
    eventType: event.event_type,
    title: event.display_title ?? titleForEvent(event.event_type),
    summary: event.summary ?? payloadString(event.payload, "message") ?? payloadString(event.payload, "summary") ?? undefined,
    status: statusFromEvent(event)
  });
}

function upsertText(items: ActivityText[], id: string, text: string): ActivityText[] {
  return upsertByKey(items, id, { id, text });
}

function mergeText(current: ActivityText[], next: ActivityText[]): ActivityText[] {
  return next.reduce((items, item) => upsertText(items, item.id, item.text), current);
}

function upsertByKey<T extends { id: string }>(items: T[], id: string, next: T): T[] {
  if (!items.some((item) => item.id === id)) {
    return [...items, next];
  }
  return items.map((item) => (item.id === id ? next : item));
}

function runTitleForEvent(event: RuntimeEventEnvelope, activity: RunActivity): string {
  if (event.event_type === "run_completed") {
    return "Run completed";
  }
  if (event.event_type === "run_failed") {
    return "Run failed";
  }
  if (event.event_type === "run_cancelled") {
    return "Run cancelled";
  }
  return activity.title === "Agent activity" ? event.display_title ?? "Agent activity" : activity.title;
}

function statusFromEvent(event: RuntimeEventEnvelope, fallback: ActivityStatus = "unknown"): ActivityStatus {
  const value = (event.status ?? payloadString(event.payload, "status") ?? event.event_type).toLowerCase();
  if (value.includes("queued")) {
    return "queued";
  }
  if (value.includes("cancel")) {
    return "cancelled";
  }
  if (value.includes("fail") || value.includes("error")) {
    return "failed";
  }
  if (value.includes("complete") || value.includes("succeed") || value.includes("final")) {
    return "completed";
  }
  if (value.includes("wait") || value.includes("approval")) {
    return "waiting";
  }
  if (value.includes("running") || value.includes("started") || value.includes("progress") || value.includes("delta")) {
    return "running";
  }
  return fallback;
}

function appendAssistantDelta(items: ChatItem[], runId: string, delta: string): ChatItem[] {
  const id = `assistant-${runId}`;
  const existing = items.find((item): item is Extract<ChatItem, { kind: "message" }> => {
    return item.kind === "message" && item.id === id;
  });
  if (!existing) {
    return [...items, { id, kind: "message", role: "assistant", text: delta }];
  }
  return items.map((item) => (item.id === id && item.kind === "message" ? { ...item, text: item.text + delta } : item));
}

function finalizeAssistantMessage(items: ChatItem[], runId: string, text: string): ChatItem[] {
  const id = `assistant-${runId}`;
  const existing = items.some((item) => item.id === id);
  if (!existing) {
    return [...items, { id, kind: "message", role: "assistant", text }];
  }
  return items.map((item) => (item.id === id && item.kind === "message" ? { ...item, text } : item));
}

function upsertById(items: ChatItem[], next: ChatItem): ChatItem[] {
  if (!items.some((item) => item.id === next.id)) {
    return [...items, next];
  }
  return items.map((item) => (item.id === next.id ? next : item));
}

function textFromPayload(payload: Record<string, unknown>, key: "message" | "delta" | "summary"): string | null {
  return payloadString(payload, key);
}

function payloadString(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function objectSummary(value: Record<string, unknown> | undefined): string | undefined {
  if (value === undefined) {
    return undefined;
  }
  const message = payloadString(value, "message") ?? payloadString(value, "content") ?? payloadString(value, "summary");
  if (message) {
    return message;
  }
  const keys = Object.keys(value);
  return keys.length > 0 ? `${keys.length} fields returned` : undefined;
}

function titleForEvent(eventType: string): string {
  return eventType
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}
