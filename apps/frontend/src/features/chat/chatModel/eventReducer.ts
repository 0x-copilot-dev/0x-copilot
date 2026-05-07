import {
  isApprovalForwardedPayload,
  isApprovalRequestedPayload,
  isMcpAuthRequiredPayload,
  isRuntimeTextPayload,
  type RuntimeEventEnvelope,
} from "@enterprise-search/api-types";
import { forwardActionFromPayload, resolveActionFromPayload } from "./approval";
import {
  appendReasoning,
  appendTextDelta,
  markPendingInteractionsCancelled,
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
import { textFromPayload } from "./recordHelpers";
import {
  hasPendingActionForRun,
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
  // PR 1.1-rev2 — ``citation_made`` events are *registration* events
  // that feed the ``citationLinkReducer`` registry directly (see
  // ``ChatScreen``), not activity-timeline events. They must not
  // produce inline tool cards in the thread — the chip in the
  // assistant's prose is the only visible affordance.
  if (event.event_type === "citation_made") {
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
  // PR 1.4 — two-stage approval forwarding. The parent's
  // `approval_resolved status=forwarded` event is what flips the original
  // inline card into the "Waiting on @marcus" pill (via the
  // resolveActionFromPayload branch above). The trailing
  // `approval_forwarded` event annotates that pill with the recipient's
  // user id + timestamp so the FE can render the caption without a fetch.
  // The new pending child row arrives via the subsequent
  // `approval_requested` event and renders as its own card.
  if (
    event.event_type === "approval_forwarded" &&
    isApprovalForwardedPayload(event.payload)
  ) {
    return forwardActionFromPayload(items, event.payload);
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
    // A run can terminate with approval / mcp_auth tool parts still
    // unresolved (user hit Stop, run failed, queue timed out). Settle
    // them locally so the cards render as "Cancelled" and the auto-
    // resume effect doesn't re-bind activeRunId to a dead run.
    const withCancelledInteractions =
      event.event_type === "run_completed"
        ? withProgress
        : markPendingInteractionsCancelled(withProgress, event.run_id);
    return settleAssistantRun(
      withCancelledInteractions,
      event,
      statusFromRuntimeEvent(event),
      metadataFromRuntimeEvent(event),
    );
  }
  if (hasPendingActionForRun(items, event.run_id)) {
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
    const replace = event.event_type === "reasoning_summary";
    const eventCreatedAtMs = eventCreatedAtToMs(event.created_at);
    const partStatus = { type: replace ? "complete" : "running" } as const;
    return updateAssistantContent(items, event, (content) =>
      appendReasoning(content, text, replace, eventCreatedAtMs, partStatus),
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

/**
 * Parse the runtime envelope's `created_at` (ISO 8601 string) to epoch
 * milliseconds for FE-side elapsed-time computation. Returns `undefined`
 * when the value is missing or malformed — callers fall back to part
 * defaults (which means "no time stamp" rather than a wrong one).
 */
function eventCreatedAtToMs(value: string | undefined): number | undefined {
  if (!value) {
    return undefined;
  }
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : undefined;
}
