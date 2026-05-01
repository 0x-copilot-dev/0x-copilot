import type {
  AssistantPerformanceMetrics,
  Message,
  RuntimeEventEnvelope,
} from "@enterprise-search/api-types";
import type {
  MessageTiming,
  MessageStatus as AssistantMessageStatus,
  ThreadMessageLike,
} from "@assistant-ui/react";
import {
  isAssistantPerformanceMetrics,
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
export type ChatThreadMessage = ThreadMessageLike & {
  parentId?: string | null;
};

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
      parentId?: string | null;
      attachments?: ChatThreadMessage["attachments"];
      metadata?: ThreadMessageLike["metadata"];
      runId?: string;
      sourceMessageId?: string | null;
      branchId?: string | null;
      status?: AssistantMessageStatus;
    }
  | {
      id: string;
      kind: "status";
      title: string;
      text?: string;
    };

type RuntimeEventsByRunId = ReadonlyMap<
  string,
  readonly RuntimeEventEnvelope[]
>;

export function messagesToChatItems(
  messages: Message[],
  eventsByRunId: RuntimeEventsByRunId = new Map(),
): ChatItem[] {
  const items: ChatItem[] = [];
  const hydratedAssistantRuns = new Set<string>();
  for (const message of messages) {
    if (message.status === "deleted") {
      continue;
    }
    if (message.role === "assistant" && message.run_id) {
      if (hydratedAssistantRuns.has(message.run_id)) {
        continue;
      }
      hydratedAssistantRuns.add(message.run_id);
      const replayed = assistantItemFromEvents(
        message,
        eventsByRunId.get(message.run_id) ?? [],
      );
      items.push(replayed ?? messageToChatItem(message));
      continue;
    }
    items.push(messageToChatItem(message));
  }
  return items;
}

export function optimisticUserMessage({
  id,
  text,
  content,
  parentId,
  attachments,
  metadata,
  sourceMessageId,
  branchId,
}: {
  id: string;
  text: string;
  content?: ThreadMessageContent;
  parentId?: string | null;
  attachments?: ChatThreadMessage["attachments"];
  metadata?: ThreadMessageLike["metadata"];
  sourceMessageId?: string | null;
  branchId?: string | null;
}): ChatItem {
  return {
    id,
    kind: "message",
    role: "user",
    content: content ?? [{ type: "text", text }],
    parentId,
    attachments,
    metadata,
    sourceMessageId,
    branchId,
  };
}

export function chatItemsToThreadMessages(
  items: ChatItem[],
  activeRunId: string | null,
): ChatThreadMessage[] {
  return items.map((item): ChatThreadMessage => {
    if (item.kind === "message") {
      return {
        id: item.id,
        role: item.role,
        content: item.content,
        parentId: item.parentId ?? undefined,
        attachments: item.attachments,
        metadata: item.metadata,
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

export function threadMessagesToChatItems(
  messages: readonly ChatThreadMessage[],
): ChatItem[] {
  return messages.map((message): ChatItem => {
    const content =
      typeof message.content === "string"
        ? ([{ type: "text", text: message.content }] as ThreadMessageContent)
        : (message.content as ThreadMessageContent);
    return {
      id: message.id ?? `local-${Date.now()}`,
      kind: "message",
      role: message.role,
      content,
      parentId: message.parentId ?? null,
      attachments: message.attachments,
      metadata: message.metadata,
      status: message.status,
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
  if (event.parent_task_id && event.activity_kind === "tool") {
    const withNestedActivity = upsertSubagentActivity(items, event);
    if (withNestedActivity !== items) {
      return withNestedActivity;
    }
  }
  if (event.parent_task_id && event.activity_kind === "reasoning") {
    const withNestedActivity = upsertSubagentActivity(items, event);
    if (withNestedActivity !== items) {
      return withNestedActivity;
    }
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
      metadataFromRuntimeEvent(event),
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
  if (isVisibleProgressEvent(event)) {
    return upsertAssistantPart(
      items,
      event,
      progressPart(event),
      statusFromRuntimeEvent(event),
    );
  }
  if (event.event_type === "run_completed") {
    return mergeAssistantMetadata(
      items,
      event,
      metadataFromRuntimeEvent(event),
    );
  }
  return items;
}

function messageToChatItem(message: Message): ChatItem {
  return {
    id: message.message_id,
    kind: "message",
    role:
      message.role === "assistant" || message.role === "user"
        ? message.role
        : "system",
    parentId: message.parent_message_id,
    sourceMessageId: message.source_message_id ?? null,
    branchId: message.branch_id ?? null,
    runId: message.run_id ?? undefined,
    content: contentFromMessage(message),
    attachments: attachmentsFromMessage(message),
    metadata: metadataFromMessage(message),
  };
}

function assistantItemFromEvents(
  message: Message,
  events: readonly RuntimeEventEnvelope[],
): Extract<ChatItem, { kind: "message" }> | null {
  if (events.length === 0) {
    return null;
  }
  const runId = message.run_id;
  if (runId === null) {
    return null;
  }
  const replayed = [...events]
    .sort((left, right) => left.sequence_no - right.sequence_no)
    .reduce(
      (current, event) => applyRuntimeEvent(current, event),
      [] as ChatItem[],
    );
  const assistant = replayed.find(
    (item): item is Extract<ChatItem, { kind: "message" }> =>
      item.kind === "message" && item.id === assistantMessageId(runId),
  );
  return assistant && assistant.content.length > 0
    ? {
        ...assistant,
        id: message.message_id,
        parentId: message.parent_message_id,
        sourceMessageId: message.source_message_id ?? null,
        branchId: message.branch_id ?? null,
        metadata: mergeMetadata(
          assistant.metadata,
          metadataFromMessage(message),
        ),
      }
    : null;
}

function contentFromMessage(message: Message): ThreadMessageContent {
  if (message.content && message.content.length > 0) {
    return message.content as ThreadMessageContent;
  }
  return [{ type: "text", text: message.content_text }];
}

function attachmentsFromMessage(
  message: Message,
): ChatThreadMessage["attachments"] {
  if (!message.attachments || message.attachments.length === 0) {
    return undefined;
  }
  return message.attachments.map((attachment) => ({
    id: attachment.id,
    type: attachment.type,
    name: attachment.name,
    contentType: attachment.content_type ?? undefined,
    content: attachment.content,
    status: { type: "complete" },
  })) as ChatThreadMessage["attachments"];
}

function metadataFromMessage(message: Message): ThreadMessageLike["metadata"] {
  const custom: Record<string, unknown> = { ...(message.metadata ?? {}) };
  if (message.quote !== undefined && message.quote !== null) {
    custom.quote = message.quote;
  }
  if (message.source_message_id) {
    custom.source_message_id = message.source_message_id;
  }
  if (message.branch_id) {
    custom.branch_id = message.branch_id;
  }
  return metadataFromCustom(custom);
}

function metadataFromRuntimeEvent(
  event: RuntimeEventEnvelope,
): ThreadMessageLike["metadata"] | undefined {
  const metrics =
    performanceMetricsFromRecord(event.payload) ??
    performanceMetricsFromRecord(event.metadata);
  return metrics
    ? metadataFromCustom({ performance_metrics: metrics })
    : undefined;
}

function metadataFromCustom(
  custom: Record<string, unknown>,
): ThreadMessageLike["metadata"] | undefined {
  const metrics = performanceMetricsFromRecord(custom);
  if (Object.keys(custom).length === 0 && !metrics) {
    return undefined;
  }
  return {
    custom,
    ...(metrics ? { timing: timingFromPerformanceMetrics(metrics) } : {}),
  };
}

function mergeMetadata(
  current: ThreadMessageLike["metadata"] | undefined,
  next: ThreadMessageLike["metadata"] | undefined,
): ThreadMessageLike["metadata"] | undefined {
  if (!current) {
    return next;
  }
  if (!next) {
    return current;
  }
  return {
    ...current,
    ...next,
    custom: {
      ...(current.custom ?? {}),
      ...(next.custom ?? {}),
    },
  };
}

function performanceMetricsFromRecord(
  value: unknown,
): AssistantPerformanceMetrics | null {
  const record = asRecord(value);
  const metrics = record.performance_metrics;
  return isAssistantPerformanceMetrics(metrics) ? metrics : null;
}

function timingFromPerformanceMetrics(
  metrics: AssistantPerformanceMetrics,
): MessageTiming {
  return {
    streamStartTime: Date.parse(metrics.started_at),
    firstTokenTime: metrics.first_chunk_at
      ? Date.parse(metrics.first_chunk_at)
      : undefined,
    totalStreamTime: metrics.duration_ms,
    tokenCount: metrics.usage?.output ?? metrics.usage?.total,
    tokensPerSecond: metrics.usage?.output_per_second,
    totalChunks: metrics.chunk_count,
    toolCallCount: 0,
  };
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

export function resolveMcpAuthSkip(
  items: ChatItem[],
  serverId: string,
): ChatItem[] {
  return items.map((item) => {
    if (item.kind !== "message") {
      return item;
    }
    let changed = false;
    const content = item.content.map((part) => {
      if (
        !isToolCallPart(part) ||
        part.toolCallId !== serverId ||
        part.toolName !== "mcp_auth_required"
      ) {
        return part;
      }
      changed = true;
      return {
        ...part,
        args: jsonArgs({
          ...asRecord(part.args),
          server_id: serverId,
          status: "skipped",
        }),
        result: { server_id: serverId, decision: "skipped" },
      };
    });
    if (!changed) {
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
  metadata?: ThreadMessageLike["metadata"],
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
        metadata,
        status,
      },
    ];
  }
  return items.map((item) =>
    item.kind === "message" && item.id === id
      ? {
          ...item,
          content: update(item.content),
          metadata: mergeMetadata(item.metadata, metadata),
          status: nextMessageStatus(item.status, status),
        }
      : item,
  );
}

function mergeAssistantMetadata(
  items: ChatItem[],
  event: RuntimeEventEnvelope,
  metadata?: ThreadMessageLike["metadata"],
): ChatItem[] {
  if (!metadata) {
    return items;
  }
  const id = assistantMessageId(event.run_id);
  return items.map((item) =>
    item.kind === "message" && item.id === id
      ? { ...item, metadata: mergeMetadata(item.metadata, metadata) }
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

function upsertSubagentActivity(
  items: ChatItem[],
  event: RuntimeEventEnvelope,
): ChatItem[] {
  const parentTaskId = event.parent_task_id;
  if (!parentTaskId) {
    return items;
  }
  let foundParent = false;
  const updated = updateAssistantContent(items, event, (content) =>
    content.map((part) => {
      if (
        !isToolCallPart(part) ||
        part.toolName !== "run_subagent" ||
        part.toolCallId !== parentTaskId
      ) {
        return part;
      }
      foundParent = true;
      const args = asRecord(part.args);
      return {
        ...part,
        args: jsonArgs({
          ...args,
          activities: upsertActivityRecord(
            recordArray(args.activities),
            subagentActivityRecord(event),
          ),
        }),
      };
    }),
  );
  return foundParent ? updated : items;
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
  const existingTaskSummary = stringValue(existingArgs.task_summary);
  const taskSummary =
    existingTaskSummary ??
    (event.event_type === "subagent_completed" ? null : summary);
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
    task_summary: taskSummary ?? null,
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

function subagentActivityRecord(
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
      result: part.result,
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

function upsertActivityRecord(
  activities: Record<string, unknown>[],
  next: Record<string, unknown>,
): Record<string, unknown>[] {
  const nextId = stringValue(next.id);
  if (!nextId) {
    return [...activities, next];
  }
  const index = activities.findIndex((activity) => activity.id === nextId);
  if (index === -1) {
    return [...activities, next];
  }
  return activities.map((activity, currentIndex) =>
    currentIndex === index
      ? { ...activity, ...withoutNullishValues(next) }
      : activity,
  );
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

function isVisibleProgressEvent(event: RuntimeEventEnvelope): boolean {
  return (
    event.event_type === "error" ||
    event.event_type === "run_failed" ||
    event.status === "failed"
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

function recordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isPlainRecord) : [];
}

function withoutNullishValues(
  value: Record<string, unknown>,
): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(value).filter(
      ([, entry]) => entry !== null && entry !== undefined,
    ),
  );
}

function summarizeRecord(value: Record<string, unknown>): string | null {
  const entries = Object.entries(value).filter(
    ([, entry]) => entry !== null && entry !== undefined,
  );
  if (entries.length === 0) {
    return null;
  }
  return entries
    .slice(0, 3)
    .map(
      ([key, entry]) => `${key.replaceAll("_", " ")}: ${inlineSummary(entry)}`,
    )
    .join(" · ");
}

function inlineSummary(value: unknown): string {
  if (Array.isArray(value)) {
    return value.length === 0 ? "[]" : `${value.length} items`;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) {
      return "empty";
    }
    return trimmed.length > 80 ? `${trimmed.slice(0, 77)}...` : trimmed;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (value && typeof value === "object") {
    return "{...}";
  }
  return String(value);
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
