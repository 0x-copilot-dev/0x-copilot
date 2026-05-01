import type {
  Message,
  RuntimeEventEnvelope,
} from "@enterprise-search/api-types";
import type {
  MessageStatus as AssistantMessageStatus,
  ThreadMessageLike,
} from "@assistant-ui/react";
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

type ThreadMessageContent = Exclude<ThreadMessageLike["content"], string>;
type ThreadMessageContentPart = ThreadMessageContent[number];
type ThreadTextPart = Extract<ThreadMessageContentPart, { type: "text" }>;
type ThreadReasoningPart = Extract<
  ThreadMessageContentPart,
  { type: "reasoning" }
>;
type ThreadToolCallPart = Extract<
  ThreadMessageContentPart,
  { type: "tool-call" }
>;
type ThreadToolCallArgs = NonNullable<ThreadToolCallPart["args"]>;

type RuntimePartStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "waiting"
  | "unknown";

export type ChatItem =
  | {
      id: string;
      kind: "message";
      role: "user" | "assistant" | "system";
      content: ThreadMessageContent;
      runId?: string;
      status?: AssistantMessageStatus;
    }
  | {
      id: string;
      kind: "status";
      title: string;
      text?: string;
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
      content: [{ type: "text", text: message.content_text }],
    }));
}

export function optimisticUserMessage(text: string): ChatItem {
  return {
    id: `local-${Date.now()}`,
    kind: "message",
    role: "user",
    content: [{ type: "text", text }],
  };
}

export function chatItemsToThreadMessages(
  items: ChatItem[],
  activeRunId: string | null,
): ThreadMessageLike[] {
  return items.map((item): ThreadMessageLike => {
    if (item.kind === "message") {
      return {
        id: item.id,
        role: item.role,
        content: item.content,
        status: statusForMessage(item, activeRunId),
      };
    }
    return {
      id: item.id,
      role: "system",
      content: [
        {
          type: "text",
          text: item.text ? `${item.title}\n${item.text}` : item.title,
        },
      ],
    };
  });
}

export function applyRuntimeEvent(
  items: ChatItem[],
  event: RuntimeEventEnvelope,
): ChatItem[] {
  if (event.activity_kind === "heartbeat") {
    return items;
  }
  if (event.visibility === "internal") {
    return items;
  }
  if (
    event.activity_kind === "mcp_auth" &&
    isMcpAuthRequiredPayload(event.payload)
  ) {
    return upsertAssistantPart(items, event, mcpAuthPart(event.payload), {
      type: "requires-action",
      reason: "interrupt",
    });
  }
  if (
    event.event_type === "approval_requested" &&
    isApprovalRequestedPayload(event.payload)
  ) {
    return upsertAssistantPart(items, event, approvalPart(event.payload), {
      type: "requires-action",
      reason: "interrupt",
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
    return updateAssistantContent(items, event, (content) =>
      appendTextDelta(content, delta),
    );
  }
  if (
    event.event_type === "final_response" &&
    isRuntimeTextPayload(event.payload)
  ) {
    const text =
      textFromPayload(event.payload, "message") ??
      textFromPayload(event.payload, "summary");
    if (!text) {
      return items;
    }
    return updateAssistantContent(
      items,
      event,
      (content) => replaceText(content, text),
      statusFromRuntimeEvent(event),
    );
  }
  if (
    event.event_type === "reasoning_summary" ||
    event.event_type === "reasoning_summary_delta"
  ) {
    const text = reasoningText(event);
    if (!text) {
      return items;
    }
    return updateAssistantContent(items, event, (content) =>
      appendReasoning(content, text, event.event_type === "reasoning_summary"),
    );
  }
  if (event.activity_kind === "tool") {
    return upsertRuntimeToolPart(items, event);
  }
  if (event.activity_kind === "subagent") {
    return upsertSubagentPart(items, event);
  }
  if (isProgressEvent(event)) {
    return upsertAssistantPart(
      items,
      event,
      progressPart(event),
      statusFromRuntimeEvent(event),
    );
  }
  return items;
}

export function resolveApprovalDecision(
  items: ChatItem[],
  approvalId: string,
  decision: "approved" | "rejected",
): ChatItem[] {
  return items.map((item) => {
    if (item.kind !== "message") {
      return item;
    }
    const content = item.content.map((part) => {
      if (!isToolCallPart(part) || part.toolCallId !== approvalId) {
        return part;
      }
      return {
        ...part,
        args: jsonArgs({
          ...asRecord(part.args),
          approval_id: approvalId,
          status: decision,
        }),
        result: { approval_id: approvalId, decision },
      };
    });
    if (content === item.content) {
      return item;
    }
    const status =
      item.status?.type === "requires-action" && !hasPendingAction(content)
        ? ({ type: "running" } satisfies AssistantMessageStatus)
        : item.status;
    return { ...item, content, status };
  });
}

function updateAssistantContent(
  items: ChatItem[],
  event: RuntimeEventEnvelope,
  update: (content: ThreadMessageContent) => ThreadMessageContent,
  status: AssistantMessageStatus = { type: "running" },
): ChatItem[] {
  const id = assistantMessageId(event.run_id);
  const existing = items.find(
    (item): item is Extract<ChatItem, { kind: "message" }> =>
      item.kind === "message" && item.id === id,
  );
  if (!existing) {
    return [
      ...items,
      {
        id,
        kind: "message",
        role: "assistant",
        runId: event.run_id,
        content: update([]),
        status,
      },
    ];
  }
  return items.map((item) =>
    item.kind === "message" && item.id === id
      ? {
          ...item,
          content: update(item.content),
          status: nextMessageStatus(item.status, status),
        }
      : item,
  );
}

function upsertAssistantPart(
  items: ChatItem[],
  event: RuntimeEventEnvelope,
  part: ThreadMessageContentPart,
  status: AssistantMessageStatus = { type: "running" },
): ChatItem[] {
  return updateAssistantContent(
    items,
    event,
    (content) => upsertPart(content, part),
    status,
  );
}

function upsertRuntimeToolPart(
  items: ChatItem[],
  event: RuntimeEventEnvelope,
): ChatItem[] {
  const callId = toolCallId(event.payload) ?? event.span_id ?? event.event_id;
  return updateAssistantContent(items, event, (content) => {
    const existing = content.find(
      (part): part is ThreadToolCallPart =>
        isToolCallPart(part) && part.toolCallId === callId,
    );
    return upsertPart(content, toolPart(event, callId, existing));
  });
}

function upsertSubagentPart(
  items: ChatItem[],
  event: RuntimeEventEnvelope,
): ChatItem[] {
  const key = subagentKeyForEvent(event);
  if (key === null) {
    return items;
  }
  return updateAssistantContent(items, event, (content) => {
    const existing = content.find(
      (part): part is ThreadToolCallPart =>
        isToolCallPart(part) &&
        part.toolName === "run_subagent" &&
        part.toolCallId === key,
    );
    const part = subagentPart(event, key, existing);
    if (part === null) {
      return content;
    }
    return upsertPart(content, part);
  });
}

function appendTextDelta(
  content: ThreadMessageContent,
  delta: string,
): ThreadMessageContent {
  const index = content.findIndex(isTextPart);
  if (index === -1) {
    return [...content, { type: "text", text: delta }];
  }
  return content.map((part, currentIndex) =>
    currentIndex === index && isTextPart(part)
      ? { ...part, text: part.text + delta }
      : part,
  );
}

function replaceText(
  content: ThreadMessageContent,
  text: string,
): ThreadMessageContent {
  const index = content.findIndex(isTextPart);
  if (index === -1) {
    return [...content, { type: "text", text }];
  }
  return content.map((part, currentIndex) =>
    currentIndex === index && isTextPart(part) ? { ...part, text } : part,
  );
}

function appendReasoning(
  content: ThreadMessageContent,
  text: string,
  replace: boolean,
): ThreadMessageContent {
  const index = content.findIndex(isReasoningPart);
  if (index === -1) {
    return [...content, { type: "reasoning", text }];
  }
  return content.map((part, currentIndex) =>
    currentIndex === index && isReasoningPart(part)
      ? { ...part, text: replace ? text : part.text + text }
      : part,
  );
}

function upsertPart(
  content: ThreadMessageContent,
  next: ThreadMessageContentPart,
): ThreadMessageContent {
  if (!isToolCallPart(next)) {
    return [...content, next];
  }
  const index = content.findIndex(
    (part) => isToolCallPart(part) && part.toolCallId === next.toolCallId,
  );
  if (index === -1) {
    return [...content, next];
  }
  return content.map((part, currentIndex) =>
    currentIndex === index ? next : part,
  );
}

function toolPart(
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
  const delta = isToolCallDeltaPayload(payload)
    ? payloadString(payload, "delta")
    : null;
  if (delta) {
    args.deltas = [...stringArray(existingArgs.deltas), delta];
  }
  const name =
    toolName(payload) ?? existing?.toolName ?? event.display_title ?? "tool";
  const result = toolResultText(payload) ?? existing?.result;
  return {
    type: "tool-call",
    toolCallId: callId,
    toolName: name,
    args: jsonArgs(args),
    argsText: JSON.stringify(args, null, 2),
    result,
    isError: status === "failed",
  };
}

function subagentPart(
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
  if (!existing && event.event_type === "subagent_progress" && !summary) {
    return null;
  }
  const status = statusFromEvent(event, stringValue(existingArgs.status));
  const args = {
    ...existingArgs,
    subagent_name: name,
    task_id: key,
    status,
    summary: summary ?? null,
  };
  return {
    type: "tool-call",
    toolCallId: key,
    toolName: "run_subagent",
    args: jsonArgs(args),
    argsText: JSON.stringify(args, null, 2),
    result: status === "completed" ? summary : existing?.result,
    isError: status === "failed",
  };
}

function progressPart(event: RuntimeEventEnvelope): ThreadToolCallPart {
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

function approvalPart(payload: Record<string, unknown>): ThreadToolCallPart {
  const args = jsonArgs({ ...payload, status: "waiting" });
  return {
    type: "tool-call",
    toolCallId: String(payload.approval_id),
    toolName: "approval_request",
    args,
    argsText: JSON.stringify(args, null, 2),
  };
}

function mcpAuthPart(payload: Record<string, unknown>): ThreadToolCallPart {
  const args = jsonArgs({ ...payload, status: "waiting" });
  return {
    type: "tool-call",
    toolCallId: String(payload.server_id),
    toolName: "mcp_auth_required",
    args,
    argsText: JSON.stringify(args, null, 2),
  };
}

function isProgressEvent(event: RuntimeEventEnvelope): boolean {
  return (
    event.activity_kind === "run" ||
    event.activity_kind === "event" ||
    event.event_type === "progress" ||
    event.event_type === "run_completed" ||
    event.event_type === "run_cancelled" ||
    event.event_type === "run_failed" ||
    event.event_type === "error"
  );
}

function statusForMessage(
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

function nextMessageStatus(
  current: AssistantMessageStatus | undefined,
  next: AssistantMessageStatus,
): AssistantMessageStatus {
  if (current?.type === "requires-action" && next.type === "running") {
    return current;
  }
  return next;
}

function statusFromRuntimeEvent(
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

function hasPendingAction(content: ThreadMessageContent): boolean {
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

function reasoningText(event: RuntimeEventEnvelope): string | null {
  if (isReasoningSummaryDeltaPayload(event.payload)) {
    return event.payload.delta;
  }
  if (isReasoningSummaryPayload(event.payload)) {
    return event.payload.summary;
  }
  return event.summary ?? null;
}

function toolName(payload: Record<string, unknown>): string | null {
  if (isToolCallPayload(payload) || isToolResultPayload(payload)) {
    return payload.tool_name;
  }
  return payloadString(payload, "tool_name");
}

function toolCallId(payload: Record<string, unknown>): string | null {
  if (
    isToolCallPayload(payload) ||
    isToolCallDeltaPayload(payload) ||
    isToolResultPayload(payload)
  ) {
    return payload.call_id;
  }
  return payloadString(payload, "call_id");
}

function toolArgs(payload: Record<string, unknown>): Record<string, unknown> {
  if (isToolCallPayload(payload) && isPlainRecord(payload.args)) {
    return payload.args;
  }
  return {};
}

function toolArgsDelta(
  payload: Record<string, unknown>,
): Record<string, unknown> {
  if (isToolCallDeltaPayload(payload) && isPlainRecord(payload.args_delta)) {
    return payload.args_delta;
  }
  return {};
}

function toolResultText(payload: Record<string, unknown>): string | undefined {
  if (!isToolResultPayload(payload)) {
    return payloadString(payload, "summary") ?? undefined;
  }
  return (
    payload.summary ?? payload.safe_message ?? objectSummary(payload.output)
  );
}

function subagentKeyForEvent(event: RuntimeEventEnvelope): string | null {
  if (isSubagentActivityPayload(event.payload)) {
    return event.payload.task_id;
  }
  return event.task_id ?? event.parent_task_id ?? event.subagent_id ?? null;
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

function statusFromEvent(
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

function assistantMessageId(runId: string): string {
  return `assistant-${runId}`;
}

function isTextPart(part: ThreadMessageContentPart): part is ThreadTextPart {
  return part.type === "text";
}

function isReasoningPart(
  part: ThreadMessageContentPart,
): part is ThreadReasoningPart {
  return part.type === "reasoning";
}

function isToolCallPart(
  part: ThreadMessageContentPart,
): part is ThreadToolCallPart {
  return part.type === "tool-call";
}

function jsonArgs(value: Record<string, unknown>): ThreadToolCallArgs {
  return value as ThreadToolCallArgs;
}

function asRecord(value: unknown): Record<string, unknown> {
  return isPlainRecord(value) ? value : {};
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function titleForEvent(eventType: string): string {
  return eventType
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}
