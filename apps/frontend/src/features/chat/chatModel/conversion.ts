import type {
  Message,
  RuntimeEventEnvelope,
} from "@enterprise-search/api-types";
import type { ThreadMessageLike } from "../runtime/types";
import { applyRuntimeEvent } from "./eventReducer";
import { mergeMetadata, metadataFromMessage } from "./metadata";
import { assistantMessageId } from "./recordHelpers";
import { statusForMessage } from "./status";
import type {
  ChatItem,
  ChatThreadMessage,
  RuntimeEventsByRunId,
  ThreadMessageContent,
} from "./types";

export function messagesToChatItems(
  messages: Message[],
  eventsByRunId: RuntimeEventsByRunId = new Map(),
): ChatItem[] {
  const items: ChatItem[] = [];
  const persistedAssistantRunIds = new Set(
    messages.flatMap((message) =>
      message.status !== "deleted" &&
      message.role === "assistant" &&
      message.run_id
        ? [message.run_id]
        : [],
    ),
  );
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
    if (
      message.run_id &&
      !persistedAssistantRunIds.has(message.run_id) &&
      !hydratedAssistantRuns.has(message.run_id)
    ) {
      const replayed = syntheticAssistantItemFromEvents(
        message,
        eventsByRunId.get(message.run_id) ?? [],
      );
      if (replayed) {
        hydratedAssistantRuns.add(message.run_id);
        items.push(replayed);
      }
    }
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
      // PR 3.1 — surface runId on the assistant message so
      // <MessageSourcesStrip /> can read this run's citations from
      // CitationsProvider (per-run registry).
      const metadata =
        item.role === "assistant" && item.runId
          ? mergeMetadata(item.metadata, { custom: { runId: item.runId } })
          : item.metadata;
      return {
        id: item.id,
        role: item.role,
        content: item.content,
        parentId: item.parentId ?? undefined,
        attachments: item.attachments,
        // PR 3.5 / G9 — surface run_id into metadata.custom so the
        // assistant-ui MessagePrimitive renderer (`AssistantMessage.tsx`)
        // can look up its run's citations for the post-prose strip.
        // We never overwrite a custom.run_id the caller already set.
        metadata: withRunIdMetadata(item.metadata, item.runId),
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
  const runId = message.run_id;
  if (runId === null) {
    return null;
  }
  const assistant = replayedAssistantItem(runId, events);
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

function syntheticAssistantItemFromEvents(
  message: Message,
  events: readonly RuntimeEventEnvelope[],
): Extract<ChatItem, { kind: "message" }> | null {
  const runId = message.run_id;
  if (runId === null) {
    return null;
  }
  const assistant = replayedAssistantItem(runId, events);
  return assistant && assistant.content.length > 0
    ? {
        ...assistant,
        parentId: message.message_id,
        sourceMessageId: message.source_message_id ?? null,
        branchId: message.branch_id ?? null,
      }
    : null;
}

function replayedAssistantItem(
  runId: string,
  events: readonly RuntimeEventEnvelope[],
): Extract<ChatItem, { kind: "message" }> | null {
  if (events.length === 0) {
    return null;
  }
  const replayed = [...events]
    .sort((left, right) => left.sequence_no - right.sequence_no)
    .reduce(
      (current, event) => applyRuntimeEvent(current, event),
      [] as ChatItem[],
    );
  return (
    replayed.find(
      (item): item is Extract<ChatItem, { kind: "message" }> =>
        item.kind === "message" && item.id === assistantMessageId(runId),
    ) ?? null
  );
}

/**
 * PR 3.5 / G9 — fold the chat-item's `runId` into `metadata.custom.run_id`
 * so the assistant-ui MessagePrimitive renderer can look it up. We do not
 * overwrite a `custom.run_id` the upstream metadata already carried (e.g.
 * messages constructed from server payloads where the field is set
 * explicitly), and we leave the metadata untouched when the item has no
 * `runId` to avoid mutating the optimistic-message shape.
 */
function withRunIdMetadata(
  metadata: ThreadMessageLike["metadata"] | undefined,
  runId: string | undefined,
): ThreadMessageLike["metadata"] | undefined {
  if (!runId) {
    return metadata;
  }
  const existingCustom = metadata?.custom ?? {};
  if (existingCustom.run_id === runId) {
    return metadata;
  }
  return {
    ...metadata,
    custom: { ...existingCustom, run_id: runId },
  };
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
