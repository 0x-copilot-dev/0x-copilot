import {
  isApprovalRequestedPayload,
  isMcpAuthRequiredPayload,
  isRuntimeTextPayload,
  type RuntimeEventEnvelope,
} from "@enterprise-search/api-types";
import { resolveActionFromPayload } from "./approval";
import {
  appendReasoning,
  appendTextDelta,
  reconcileFinalText,
  settleAssistantRun,
  updateAssistantContent,
  upsertApprovalPart,
  upsertAssistantPart,
  upsertMcpAuthPart,
  upsertRuntimeToolPart,
  upsertSubagentActivity,
  upsertSubagentPart,
} from "./contentBuilders";
import { isLargeResultArtifactToolEvent } from "./largeArtifact";
import { metadataFromRuntimeEvent } from "./metadata";
import { progressPart } from "./partFactories";
import { reasoningText } from "./payloadHelpers";
import {
  isInternalCheckpointDelta,
  patchToolPartPresentation,
} from "./presentation";
import { assistantMessageId, textFromPayload } from "./recordHelpers";
import {
  hasPendingAction,
  isTerminalRunEvent,
  isVisibleProgressEvent,
  statusFromRuntimeEvent,
} from "./status";
import type { ChatItem } from "./types";

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
  if (event.event_type === "presentation_updated") {
    return patchToolPartPresentation(items, event);
  }
  if (
    event.activity_kind === "mcp_auth" &&
    isMcpAuthRequiredPayload(event.payload)
  ) {
    return upsertMcpAuthPart(items, event, event.payload);
  }
  if (
    event.event_type === "approval_requested" &&
    isApprovalRequestedPayload(event.payload)
  ) {
    return upsertApprovalPart(items, event, event.payload);
  }
  if (event.event_type === "approval_resolved") {
    return resolveActionFromPayload(items, event.payload);
  }
  if (isTerminalRunEvent(event)) {
    const withProgress = isVisibleProgressEvent(event)
      ? upsertAssistantPart(
          items,
          event,
          progressPart(event),
          statusFromRuntimeEvent(event),
        )
      : items;
    return settleAssistantRun(
      withProgress,
      event,
      statusFromRuntimeEvent(event),
      metadataFromRuntimeEvent(event),
    );
  }
  if (hasPendingActionForRun(items, event)) {
    return items;
  }
  if (event.activity_kind === "tool" && isLargeResultArtifactToolEvent(event)) {
    return items;
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
    if (isInternalCheckpointDelta(delta)) {
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
      (content) => reconcileFinalText(content, text),
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
  return items;
}

function hasPendingActionForRun(
  items: ChatItem[],
  event: RuntimeEventEnvelope,
): boolean {
  const id = assistantMessageId(event.run_id);
  const assistant = items.find(
    (item): item is Extract<ChatItem, { kind: "message" }> =>
      item.kind === "message" && item.id === id,
  );
  return assistant ? hasPendingAction(assistant.content) : false;
}
