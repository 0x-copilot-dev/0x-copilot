import type { RuntimeEventEnvelope } from "@enterprise-search/api-types";
import type {
  MessageStatus as AssistantMessageStatus,
  ThreadMessageLike,
} from "@assistant-ui/react";
import { asRecord, stringValue } from "../utils/jsonUtils";
import { mcpApprovalMatchesWrapper, mcpAuthMatchesWrapper } from "./mcpAuth";
import { mergeMetadata } from "./metadata";
import {
  approvalPart,
  mcpAuthPart,
  subagentActivityRecord,
  subagentPart,
  toolPart,
} from "./partFactories";
import { toolCallId } from "./payloadHelpers";
import {
  assistantMessageId,
  isReasoningPart,
  isTextPart,
  isToolCallPart,
  jsonArgs,
  recordArray,
  withoutNullishValues,
} from "./recordHelpers";
import { nextMessageStatus } from "./status";
import { subagentKeyForEvent } from "./subagentText";
import type {
  ChatItem,
  ThreadMessageContent,
  ThreadMessageContentPart,
  ThreadToolCallPart,
} from "./types";

export function updateAssistantContent(
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
  return items.map((item) => {
    if (item.kind !== "message" || item.id !== id) {
      return item;
    }
    const content = update(item.content);
    return {
      ...item,
      content,
      metadata: mergeMetadata(item.metadata, metadata),
      status: nextMessageStatus(item.status, status, content),
    };
  });
}

export function settleAssistantRun(
  items: ChatItem[],
  event: RuntimeEventEnvelope,
  status: AssistantMessageStatus,
  metadata?: ThreadMessageLike["metadata"],
): ChatItem[] {
  const id = assistantMessageId(event.run_id);
  const existing = items.find(
    (item): item is Extract<ChatItem, { kind: "message" }> =>
      item.kind === "message" && item.id === id,
  );
  if (!existing) {
    return items;
  }
  return items.map((item) =>
    item.kind === "message" && item.id === id
      ? {
          ...item,
          metadata: mergeMetadata(item.metadata, metadata),
          status,
        }
      : item,
  );
}

export function upsertAssistantPart(
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

export function upsertApprovalPart(
  items: ChatItem[],
  event: RuntimeEventEnvelope,
  payload: Record<string, unknown>,
): ChatItem[] {
  const sourceToolCallId = stringValue(payload.source_tool_call_id);
  const nextPart = approvalPart(payload, event.presentation ?? null);
  return updateAssistantContent(
    items,
    event,
    (content) =>
      replaceToolCallPart(content, sourceToolCallId, nextPart, (part) =>
        mcpApprovalMatchesWrapper(part, payload),
      ),
    { type: "requires-action", reason: "interrupt" },
  );
}

export function upsertMcpAuthPart(
  items: ChatItem[],
  event: RuntimeEventEnvelope,
  payload: Record<string, unknown>,
): ChatItem[] {
  const sourceToolCallId = stringValue(payload.source_tool_call_id);
  const nextPart = mcpAuthPart(payload, event.presentation ?? null);
  return updateAssistantContent(
    items,
    event,
    (content) =>
      replaceToolCallPart(content, sourceToolCallId, nextPart, (part) =>
        mcpAuthMatchesWrapper(part, payload),
      ),
    { type: "requires-action", reason: "interrupt" },
  );
}

export function replaceToolCallPart(
  content: ThreadMessageContent,
  toolCallId: string | null,
  next: ThreadToolCallPart,
  fallbackMatch?: (part: ThreadToolCallPart) => boolean,
): ThreadMessageContent {
  if (!toolCallId) {
    return replaceFirstMatchingToolPart(content, next, fallbackMatch);
  }
  const index = content.findIndex(
    (part) => isToolCallPart(part) && part.toolCallId === toolCallId,
  );
  if (index === -1) {
    return replaceFirstMatchingToolPart(content, next, fallbackMatch);
  }
  return content.map((part, currentIndex) =>
    currentIndex === index ? next : part,
  );
}

export function replaceFirstMatchingToolPart(
  content: ThreadMessageContent,
  next: ThreadToolCallPart,
  fallbackMatch?: (part: ThreadToolCallPart) => boolean,
): ThreadMessageContent {
  if (!fallbackMatch) {
    return upsertPart(content, next);
  }
  const index = content.findIndex(
    (part) => isToolCallPart(part) && fallbackMatch(part),
  );
  if (index === -1) {
    return upsertPart(content, next);
  }
  return content.map((part, currentIndex) =>
    currentIndex === index ? next : part,
  );
}

export function upsertRuntimeToolPart(
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

export function upsertSubagentPart(
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

export function upsertSubagentActivity(
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

export function appendTextDelta(
  content: ThreadMessageContent,
  delta: string,
): ThreadMessageContent {
  if (content.length === 0) {
    return [{ type: "text", text: delta }];
  }
  const index = content.length - 1;
  const last = content[index];
  if (!isTextPart(last)) {
    return [...content, { type: "text", text: delta }];
  }
  return content.map((part, currentIndex) =>
    currentIndex === index && isTextPart(part)
      ? { ...part, text: part.text + delta }
      : part,
  );
}

export function reconcileFinalText(
  content: ThreadMessageContent,
  text: string,
): ThreadMessageContent {
  const lastTextIndex = lastTextPartIndex(content);
  if (lastTextIndex === -1) {
    return [...content, { type: "text", text }];
  }
  if (lastTextIndex !== content.length - 1) {
    return [...content, { type: "text", text }];
  }
  return content.map((part, currentIndex) =>
    currentIndex === lastTextIndex && isTextPart(part)
      ? { ...part, text }
      : part,
  );
}

function lastTextPartIndex(content: ThreadMessageContent): number {
  for (let index = content.length - 1; index >= 0; index -= 1) {
    if (isTextPart(content[index])) {
      return index;
    }
  }
  return -1;
}

export function appendReasoning(
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

export function upsertPart(
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

export function upsertActivityRecord(
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
