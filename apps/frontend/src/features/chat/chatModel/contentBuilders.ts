import type { RuntimeEventEnvelope } from "@enterprise-search/api-types";
import type {
  MessageStatus as AssistantMessageStatus,
  ThreadMessageLike,
} from "../runtime/types";
import { asRecord, stringValue } from "../utils/jsonUtils";
import { mcpApprovalMatchesWrapper, mcpAuthMatchesWrapper } from "./mcpAuth";
import { mergeMetadata } from "./metadata";
import {
  approvalPart,
  mcpAuthPart,
  subagentActivityRecord,
  subagentFleetPart,
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
  // PR A2 — fleet bookend events (`subagent_fleet_started` /
  // `subagent_fleet_finished`) own a sibling `run_subagent_fleet` part keyed
  // by `fleet_id`; child `subagent_*` events keep their own `run_subagent`
  // parts so the singleton path (without a fleet) still works unchanged.
  if (
    event.event_type === "subagent_fleet_started" ||
    event.event_type === "subagent_fleet_finished"
  ) {
    return upsertSubagentFleetPart(items, event);
  }
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

function upsertSubagentFleetPart(
  items: ChatItem[],
  event: RuntimeEventEnvelope,
): ChatItem[] {
  const fleetId = fleetIdFromEvent(event);
  if (fleetId === null) {
    return items;
  }
  return updateAssistantContent(items, event, (content) => {
    const existing = content.find(
      (part): part is ThreadToolCallPart =>
        isToolCallPart(part) &&
        part.toolName === "run_subagent_fleet" &&
        part.toolCallId === fleetId,
    );
    const part = subagentFleetPart(event, fleetId, existing);
    return upsertPart(content, part);
  });
}

function fleetIdFromEvent(event: RuntimeEventEnvelope): string | null {
  const payload = event.payload;
  if (
    payload === null ||
    typeof payload !== "object" ||
    Array.isArray(payload)
  ) {
    return null;
  }
  const raw = (payload as Record<string, unknown>).fleet_id;
  if (typeof raw !== "string") {
    return null;
  }
  const trimmed = raw.trim();
  return trimmed.length > 0 ? trimmed : null;
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
  // Visible text means the model has stopped thinking — flip any open
  // reasoning span to `complete` even if the provider didn't emit an
  // explicit close marker.
  const settled = closeReasoningIfRunning(content);
  if (settled.length === 0) {
    return [{ type: "text", text: delta }];
  }
  const index = settled.length - 1;
  const last = settled[index];
  if (!isTextPart(last)) {
    return [...settled, { type: "text", text: delta }];
  }
  return settled.map((part, currentIndex) =>
    currentIndex === index && isTextPart(part)
      ? { ...part, text: part.text + delta }
      : part,
  );
}

export function reconcileFinalText(
  content: ThreadMessageContent,
  text: string,
): ThreadMessageContent {
  const settled = closeReasoningIfRunning(content);
  const lastTextIndex = lastTextPartIndex(settled);
  if (lastTextIndex === -1) {
    return [...settled, { type: "text", text }];
  }
  if (lastTextIndex !== settled.length - 1) {
    return [...settled, { type: "text", text }];
  }
  return settled.map((part, currentIndex) =>
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
  eventCreatedAtMs?: number,
  partStatus?: { type: "running" | "complete" },
): ThreadMessageContent {
  const status = partStatus ?? { type: replace ? "complete" : "running" };
  const index = content.findIndex(isReasoningPart);
  if (index === -1) {
    return [
      ...content,
      {
        type: "reasoning",
        text,
        status,
        startedAtMs: eventCreatedAtMs,
        updatedAtMs: eventCreatedAtMs,
      },
    ];
  }
  return content.map((part, currentIndex) =>
    currentIndex === index && isReasoningPart(part)
      ? {
          ...part,
          text: replace ? text : part.text + text,
          status,
          startedAtMs: part.startedAtMs ?? eventCreatedAtMs,
          updatedAtMs: eventCreatedAtMs ?? part.updatedAtMs,
        }
      : part,
  );
}

/**
 * Flip any running reasoning part to `complete`. Called when a
 * non-reasoning event (text delta, tool call, final response) lands on
 * the same assistant message — covers the case where the provider never
 * emits an explicit close marker (`thinking_signature` /
 * `reasoning_summary_text_done`) and the BE therefore can't emit a final
 * `reasoning_summary` cap. Idempotent.
 */
export function closeReasoningIfRunning(
  content: ThreadMessageContent,
): ThreadMessageContent {
  let changed = false;
  const next = content.map((part) => {
    if (!isReasoningPart(part) || part.status?.type !== "running") {
      return part;
    }
    changed = true;
    return { ...part, status: { type: "complete" } as const };
  });
  return changed ? next : content;
}

export function upsertPart(
  content: ThreadMessageContent,
  next: ThreadMessageContentPart,
): ThreadMessageContent {
  // A tool-call dispatch (or any non-reasoning part) closes any open
  // reasoning span — the model has finished thinking and is acting.
  const settled = isToolCallPart(next)
    ? closeReasoningIfRunning(content)
    : content;
  if (!isToolCallPart(next)) {
    return [...settled, next];
  }
  const index = settled.findIndex(
    (part) => isToolCallPart(part) && part.toolCallId === next.toolCallId,
  );
  if (index === -1) {
    return [...settled, next];
  }
  return settled.map((part, currentIndex) =>
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
