import type {
  ApprovalRequestedPayload,
  McpAuthRequiredEventPayload,
  Message,
  RuntimeEventEnvelope
} from "@enterprise-search/api-types";
import {
  isApprovalRequestedPayload,
  isRuntimeTextPayload
} from "@enterprise-search/api-types";
import { isMcpAuthRequiredPayload } from "../../api/mcpApi";

export type ChatItem =
  | {
      id: string;
      kind: "message";
      role: "user" | "assistant" | "system";
      text: string;
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
    const text = textFromPayload(event.payload, "message") ?? textFromPayload(event.payload, "summary");
    if (!text) {
      return items;
    }
    return finalizeAssistantMessage(items, event.run_id, text);
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
  const value = payload[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function titleForEvent(eventType: string): string {
  return eventType
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}
