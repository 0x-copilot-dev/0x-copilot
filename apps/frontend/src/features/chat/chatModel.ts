import type {
  ApprovalRequestedPayload,
  McpAuthRequiredEventPayload,
  Message,
  RuntimeEventEnvelope,
} from "@enterprise-search/api-types";
import {
  isApprovalRequestedPayload,
  isMcpAuthRequiredPayload,
  isReasoningSummaryDeltaPayload,
  isReasoningSummaryPayload,
  isRuntimeTextPayload,
  isSubagentActivityPayload,
  isToolCallDeltaPayload,
  isToolCallPayload,
  isToolResultPayload,
} from "@enterprise-search/api-types";

export type ActivityStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "waiting"
  | "unknown";

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
      role:
        message.role === "assistant" || message.role === "user"
          ? message.role
          : "system",
      text: message.content_text,
    }));
}

export function optimisticUserMessage(text: string): ChatItem {
  return {
    id: `local-${Date.now()}`,
    kind: "message",
    role: "user",
    text,
  };
}

export function applyRuntimeEvent(
  items: ChatItem[],
  event: RuntimeEventEnvelope,
): ChatItem[] {
  if (event.activity_kind === "heartbeat") {
    return items;
  }
  if (
    event.activity_kind === "mcp_auth" &&
    isMcpAuthRequiredPayload(event.payload)
  ) {
    return upsertById(items, {
      id: event.event_id,
      kind: "mcp-auth",
      payload: event.payload,
    });
  }
  if (
    event.event_type === "approval_requested" &&
    isApprovalRequestedPayload(event.payload)
  ) {
    return upsertById(items, {
      id: event.event_id,
      kind: "approval",
      payload: event.payload,
    });
  }
  if (
    event.event_type === "model_delta" &&
    isRuntimeTextPayload(event.payload)
  ) {
    const delta = textFromPayload(event.payload, "delta");
    if (!delta) {
      return items;
    }
    return appendAssistantDelta(items, event.run_id, delta);
  }
  if (
    event.event_type === "final_response" &&
    isRuntimeTextPayload(event.payload)
  ) {
    const withActivity = upsertRunActivity(items, event);
    const text =
      textFromPayload(event.payload, "message") ??
      textFromPayload(event.payload, "summary");
    if (!text) {
      return withActivity;
    }
    return finalizeAssistantMessage(withActivity, event.run_id, text);
  }
  if (event.event_type === "run_completed") {
    return removeRunActivity(items, event.run_id);
  }
  if (isActivityEvent(event)) {
    return upsertRunActivity(items, event);
  }
  if (isRuntimeTextPayload(event.payload)) {
    const title = event.display_title ?? titleForEvent(event.event_type);
    const text =
      textFromPayload(event.payload, "message") ?? event.summary ?? undefined;
    return upsertById(items, {
      id: event.event_id,
      kind: "status",
      title,
      text,
    });
  }
  return items;
}

function isActivityEvent(event: RuntimeEventEnvelope): boolean {
  if (event.visibility === "internal") {
    return false;
  }
  return ["run", "tool", "subagent", "reasoning", "approval", "event"].includes(
    event.activity_kind,
  );
}

function upsertRunActivity(
  items: ChatItem[],
  event: RuntimeEventEnvelope,
): ChatItem[] {
  const id = `activity-${event.run_id}`;
  const existing = items.find(
    (item): item is Extract<ChatItem, { kind: "run-activity" }> => {
      return item.kind === "run-activity" && item.id === id;
    },
  );
  const activity = applyActivityEvent(
    existing?.activity ?? createRunActivity(event),
    event,
  );
  if (!existing) {
    return [...items, { id, kind: "run-activity", activity }];
  }
  return items.map((item) =>
    item.id === id && item.kind === "run-activity"
      ? { ...item, activity }
      : item,
  );
}

function removeRunActivity(items: ChatItem[], runId: string): ChatItem[] {
  return items.filter((item) => item.id !== `activity-${runId}`);
}

function createRunActivity(event: RuntimeEventEnvelope): RunActivity {
  return {
    runId: event.run_id,
    status: runStatusFromEvent(event),
    title: runTitleForEvent(event),
    summary: runSummaryForEvent(event),
    reasoning: [],
    events: [],
    tools: [],
    subagents: [],
  };
}

function applyActivityEvent(
  activity: RunActivity,
  event: RuntimeEventEnvelope,
): RunActivity {
  let next: RunActivity = {
    ...activity,
    status: runStatusFromEvent(event, activity.status),
    title: runTitleForEvent(event, activity),
    summary: runSummaryForEvent(event) ?? activity.summary,
    reasoning: [...activity.reasoning],
    events: [...activity.events],
    tools: [...activity.tools],
    subagents: activity.subagents.map((subagent) => ({
      ...subagent,
      reasoning: [...subagent.reasoning],
      events: [...subagent.events],
      tools: subagent.tools.map((tool) => ({
        ...tool,
        deltas: [...tool.deltas],
      })),
    })),
  };

  if (event.activity_kind === "reasoning") {
    return appendReasoning(next, event);
  }
  if (event.activity_kind === "tool") {
    return upsertTool(next, event);
  }
  if (event.activity_kind === "subagent") {
    if (
      isInternalSubagentProgress(event) &&
      subagentKeyForActivity(next, event) === null
    ) {
      return next;
    }
    return upsertSubagent(next, event);
  }
  if (event.activity_kind !== "message") {
    next = appendActivityRow(next, event);
  }
  return next;
}

function appendReasoning(
  activity: RunActivity,
  event: RuntimeEventEnvelope,
): RunActivity {
  const text = reasoningText(event);
  if (!text) {
    return activity;
  }
  const subagentKey = subagentKeyForActivity(activity, event);
  if (subagentKey) {
    const withSubagent = ensureSubagent(activity, event, subagentKey);
    return {
      ...withSubagent,
      subagents: withSubagent.subagents.map((subagent) =>
        subagent.id === subagentKey
          ? {
              ...subagent,
              reasoning: upsertText(subagent.reasoning, event.event_id, text),
            }
          : subagent,
      ),
    };
  }
  return {
    ...activity,
    reasoning: upsertText(activity.reasoning, event.event_id, text),
  };
}

function upsertTool(
  activity: RunActivity,
  event: RuntimeEventEnvelope,
): RunActivity {
  if (isInternalEvent(event)) {
    return activity;
  }
  const tool = toolFromEvent(event);
  const subagentKey = subagentKeyForActivity(activity, event);
  if (subagentKey) {
    const withSubagent = ensureSubagent(activity, event, subagentKey);
    return {
      ...withSubagent,
      subagents: withSubagent.subagents.map((subagent) =>
        subagent.id === subagentKey
          ? {
              ...subagent,
              tools: upsertToolInList(subagent.tools, tool, event),
            }
          : subagent,
      ),
    };
  }
  return { ...activity, tools: upsertToolInList(activity.tools, tool, event) };
}

function upsertSubagent(
  activity: RunActivity,
  event: RuntimeEventEnvelope,
): RunActivity {
  const key = subagentKeyForActivity(activity, event);
  if (key === null) {
    return appendActivityRow(activity, event);
  }
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
        summary:
          event.summary ??
          payloadString(event.payload, "summary") ??
          subagent.summary,
        events: isInternalSubagentProgress(event)
          ? subagent.events
          : upsertEvent(subagent.events, event),
      };
    }),
  };
}

function appendActivityRow(
  activity: RunActivity,
  event: RuntimeEventEnvelope,
): RunActivity {
  const subagentKey = subagentKeyForActivity(activity, event);
  if (subagentKey) {
    const withSubagent = ensureSubagent(activity, event, subagentKey);
    return {
      ...withSubagent,
      subagents: withSubagent.subagents.map((subagent) =>
        subagent.id === subagentKey
          ? { ...subagent, events: upsertEvent(subagent.events, event) }
          : subagent,
      ),
    };
  }
  return { ...activity, events: upsertEvent(activity.events, event) };
}

function ensureSubagent(
  activity: RunActivity,
  event: RuntimeEventEnvelope,
  key: string,
): RunActivity {
  if (activity.subagents.some((subagent) => subagent.id === key)) {
    return activity;
  }
  const name = subagentNameForEvent(event) ?? "Subagent";
  return {
    ...activity,
    subagents: [
      ...activity.subagents,
      {
        id: key,
        taskId:
          event.task_id ??
          (isSubagentActivityPayload(event.payload)
            ? event.payload.task_id
            : key),
        name,
        status: statusFromEvent(event),
        summary:
          event.summary ?? payloadString(event.payload, "summary") ?? undefined,
        reasoning: [],
        events: [],
        tools: [],
      },
    ],
  };
}

function upsertToolInList(
  tools: ToolCallActivity[],
  tool: ToolCallActivity,
  event: RuntimeEventEnvelope,
): ToolCallActivity[] {
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
      deltas: mergeText(item.deltas, tool.deltas),
    };
  });
}

function toolFromEvent(event: RuntimeEventEnvelope): ToolCallActivity {
  const payload = event.payload;
  const fallbackId = event.span_id ?? event.event_id;
  const name = toolName(payload) ?? event.display_title ?? "tool";
  return {
    id: toolCallId(payload) ?? fallbackId,
    name,
    status: statusFromEvent(event),
    summary: event.summary ?? payloadString(payload, "summary") ?? undefined,
    result: toolResultText(payload),
    deltas: [],
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
  if (
    isToolCallPayload(payload) ||
    isToolCallDeltaPayload(payload) ||
    isToolResultPayload(payload)
  ) {
    return payload.call_id;
  }
  const value = payload.call_id;
  return typeof value === "string" && value.trim() ? value : null;
}

function toolResultText(payload: Record<string, unknown>): string | undefined {
  if (!isToolResultPayload(payload)) {
    return payloadString(payload, "summary") ?? undefined;
  }
  return (
    payload.summary ?? payload.safe_message ?? objectSummary(payload.output)
  );
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
  const name = subagentNameForEvent(event);
  if (isSubagentActivityPayload(event.payload) && name !== null) {
    return event.payload.task_id;
  }
  if (name !== null) {
    return event.task_id ?? event.parent_task_id ?? event.subagent_id ?? null;
  }
  return null;
}

function subagentKeyForActivity(
  activity: RunActivity,
  event: RuntimeEventEnvelope,
): string | null {
  return (
    subagentKeyForEvent(event) ?? existingSubagentKeyForEvent(activity, event)
  );
}

function existingSubagentKeyForEvent(
  activity: RunActivity,
  event: RuntimeEventEnvelope,
): string | null {
  const candidates = [
    isSubagentActivityPayload(event.payload) ? event.payload.task_id : null,
    event.task_id ?? null,
    event.parent_task_id ?? null,
    event.subagent_id ?? null,
  ];
  return (
    candidates.find((candidate) =>
      activity.subagents.some((subagent) => subagent.id === candidate),
    ) ?? null
  );
}

function subagentNameForEvent(event: RuntimeEventEnvelope): string | null {
  const payloadName = isSubagentActivityPayload(event.payload)
    ? (event.payload.subagent_name ?? event.payload.subagent_id)
    : undefined;
  return (
    meaningfulSubagentName(payloadName) ??
    meaningfulSubagentName(event.subagent_id)
  );
}

function meaningfulSubagentName(
  value: string | null | undefined,
): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  if (!trimmed || trimmed.toLowerCase() === "subagent") {
    return null;
  }
  return trimmed;
}

function upsertEvent(
  events: ActivityEvent[],
  event: RuntimeEventEnvelope,
): ActivityEvent[] {
  return upsertByKey(events, event.event_id, {
    id: event.event_id,
    eventType: event.event_type,
    title: event.display_title ?? titleForEvent(event.event_type),
    summary:
      event.summary ??
      payloadString(event.payload, "message") ??
      payloadString(event.payload, "summary") ??
      undefined,
    status: statusFromEvent(event),
  });
}

function isInternalSubagentProgress(event: RuntimeEventEnvelope): boolean {
  if (isInternalEvent(event)) {
    return true;
  }
  const text =
    payloadString(event.payload, "message") ??
    payloadString(event.payload, "summary") ??
    event.summary ??
    "";
  return event.event_type === "subagent_progress" && !text;
}

function isInternalEvent(event: RuntimeEventEnvelope): boolean {
  return event.visibility === "internal";
}

function upsertText(
  items: ActivityText[],
  id: string,
  text: string,
): ActivityText[] {
  return upsertByKey(items, id, { id, text });
}

function mergeText(
  current: ActivityText[],
  next: ActivityText[],
): ActivityText[] {
  return next.reduce(
    (items, item) => upsertText(items, item.id, item.text),
    current,
  );
}

function upsertByKey<T extends { id: string }>(
  items: T[],
  id: string,
  next: T,
): T[] {
  if (!items.some((item) => item.id === id)) {
    return [...items, next];
  }
  return items.map((item) => (item.id === id ? next : item));
}

function runTitleForEvent(event: RuntimeEventEnvelope): string;
function runTitleForEvent(
  event: RuntimeEventEnvelope,
  activity: RunActivity,
): string;
function runTitleForEvent(
  event: RuntimeEventEnvelope,
  activity?: RunActivity,
): string {
  if (event.event_type === "run_completed") {
    return "Run completed";
  }
  if (event.event_type === "run_failed") {
    return "Run failed";
  }
  if (event.event_type === "run_cancelled") {
    return "Run cancelled";
  }
  if (event.activity_kind === "run") {
    return event.display_title ?? titleForEvent(event.event_type);
  }
  return activity?.title ?? "Agent activity";
}

function runSummaryForEvent(event: RuntimeEventEnvelope): string | undefined {
  return event.activity_kind === "run"
    ? (event.summary ?? undefined)
    : undefined;
}

function runStatusFromEvent(
  event: RuntimeEventEnvelope,
  fallback: ActivityStatus = "running",
): ActivityStatus {
  if (event.activity_kind === "run" || event.event_type === "final_response") {
    return statusFromEvent(event, fallback);
  }
  return fallback;
}

function statusFromEvent(
  event: RuntimeEventEnvelope,
  fallback: ActivityStatus = "unknown",
): ActivityStatus {
  const value = (
    event.status ??
    payloadString(event.payload, "status") ??
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
  return fallback;
}

function appendAssistantDelta(
  items: ChatItem[],
  runId: string,
  delta: string,
): ChatItem[] {
  const id = `assistant-${runId}`;
  const existing = items.find(
    (item): item is Extract<ChatItem, { kind: "message" }> => {
      return item.kind === "message" && item.id === id;
    },
  );
  if (!existing) {
    return [...items, { id, kind: "message", role: "assistant", text: delta }];
  }
  return items.map((item) =>
    item.id === id && item.kind === "message"
      ? { ...item, text: item.text + delta }
      : item,
  );
}

function finalizeAssistantMessage(
  items: ChatItem[],
  runId: string,
  text: string,
): ChatItem[] {
  const id = `assistant-${runId}`;
  const existing = items.some((item) => item.id === id);
  if (!existing) {
    return [...items, { id, kind: "message", role: "assistant", text }];
  }
  return items.map((item) =>
    item.id === id && item.kind === "message" ? { ...item, text } : item,
  );
}

function upsertById(items: ChatItem[], next: ChatItem): ChatItem[] {
  if (!items.some((item) => item.id === next.id)) {
    return [...items, next];
  }
  return items.map((item) => (item.id === next.id ? next : item));
}

function textFromPayload(
  payload: Record<string, unknown>,
  key: "message" | "delta" | "summary",
): string | null {
  return payloadString(payload, key);
}

function payloadString(
  payload: Record<string, unknown>,
  key: string,
): string | null {
  const value = payload[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function objectSummary(
  value: Record<string, unknown> | undefined,
): string | undefined {
  if (value === undefined) {
    return undefined;
  }
  const message =
    payloadString(value, "message") ??
    payloadString(value, "content") ??
    payloadString(value, "summary");
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
