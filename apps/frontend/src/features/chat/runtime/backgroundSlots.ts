import type { RuntimeEventEnvelope } from "@enterprise-search/api-types";
import { applyCitationEvent } from "../chatModel/citationReducer";
import {
  applyCitationLinkEvent,
  emptyCitationLinkRegistry,
  type CitationLinkRegistryByRun,
} from "../chatModel/citationLinkReducer";
import {
  emptyCitationRegistry,
  type CitationRegistryByRun,
} from "@enterprise-search/chat-surface";
import { applyRuntimeEvent, type ChatItem } from "../chatModel";
import {
  applySourceEvent,
  emptySourceMap,
  type SourceEntryMap,
} from "../chatModel/sourcesReducer";
import { isRunUiEvent } from "../chatRunState";

/**
 * PR 2.2.1 — Background slots store.
 *
 * Holds the per-conversation runtime state for **non-visible** chats so
 * their SSE streams can keep running while the user works in another
 * thread. The visible conversation continues to live in `ChatScreen`'s
 * useState hooks; on switch-away we freeze the visible state into a slot
 * here, and on switch-back we thaw it. Stream events arriving for a
 * non-visible run are routed into the matching slot via `applyEvent`,
 * so a backgrounded run keeps accumulating tokens, citations, and
 * sources without the UI being mounted on it.
 *
 * The store also owns the per-run stream registry: `{runId →
 * AgentEventStream + reconnectTimer}` plus the inverse `runId →
 * conversationId` lookup that the `handleEvent` router uses to decide
 * "visible setter, or background slot?" without leaking conv-id captures
 * into the SSE callback.
 *
 * No SSE close happens on conv switch — see `pr-2.2.1` PRD §3.2 for the
 * lifecycle invariants.
 */

export interface BackgroundSlot {
  items: ChatItem[];
  citations: CitationRegistryByRun;
  // PR 1.1-rev2 — model-declared citation links registry, frozen
  // alongside the legacy citation registry so warm-thaw restores both.
  citationLinks: CitationLinkRegistryByRun;
  sources: SourceEntryMap;
  activeRunId: string | null;
  latestRunEvent: RuntimeEventEnvelope | null;
  /** runId → user message id for `withAssistantParent` rebinding. */
  userMessageIdByRunId: Map<string, string>;
  /** runId → highest applied sequence_no, for `?after_sequence=N` resume. */
  latestSequenceByRunId: Map<string, number>;
  /** Per-conv status string surfaced in topbar when this slot is bound. */
  status: string;
  /** When the slot was last visible (epoch ms); newest = lowest priority for eviction. */
  lastVisibleAt: number;
}

export function emptySlot(): BackgroundSlot {
  return {
    items: [],
    citations: emptyCitationRegistry(),
    citationLinks: emptyCitationLinkRegistry(),
    sources: emptySourceMap(),
    activeRunId: null,
    latestRunEvent: null,
    userMessageIdByRunId: new Map(),
    latestSequenceByRunId: new Map(),
    status: "Ready",
    lastVisibleAt: Date.now(),
  };
}

/**
 * Apply a single runtime event to a slot. Mirrors the four reducer
 * dispatches in `ChatScreen.handleEvent`. Pure-ish — mutates Map inputs
 * (`userMessageIdByRunId`, `latestSequenceByRunId`) by replacement so
 * React `setState` consumers still see fresh references.
 */
export function applyEventToSlot(
  slot: BackgroundSlot,
  event: RuntimeEventEnvelope,
): BackgroundSlot {
  const userMessageIdByRunId = slot.userMessageIdByRunId;
  const parentMessageId = userMessageIdByRunId.get(event.run_id) ?? null;
  const latestSequenceByRunId = new Map(slot.latestSequenceByRunId);
  latestSequenceByRunId.set(
    event.run_id,
    Math.max(latestSequenceByRunId.get(event.run_id) ?? 0, event.sequence_no),
  );

  return {
    ...slot,
    items: rebindAssistantParent(
      applyRuntimeEvent(slot.items, event),
      event.run_id,
      parentMessageId,
    ),
    citations: applyCitationEvent(slot.citations, event),
    citationLinks: applyCitationLinkEvent(slot.citationLinks, event),
    sources: applySourceEvent(slot.sources, event),
    latestRunEvent: isRunUiEvent(event) ? event : slot.latestRunEvent,
    latestSequenceByRunId,
  };
}

/**
 * Mirrors `ChatScreen.withAssistantParent`: re-parent an assistant
 * message that came over the wire without a parent_id, so thread
 * rendering keeps the parent/child chain intact. Inlined here to avoid
 * an export-only refactor of `ChatScreen` for one helper.
 */
function rebindAssistantParent(
  items: ChatItem[],
  runId: string,
  parentMessageId: string | null,
): ChatItem[] {
  if (parentMessageId === null) {
    return items;
  }
  return items.map((item) =>
    item.kind === "message" &&
    item.role === "assistant" &&
    item.runId === runId &&
    !item.parentId
      ? { ...item, parentId: parentMessageId }
      : item,
  );
}

/**
 * Mutate a slot for a terminal run event. Clears `activeRunId` if the
 * terminal run was the slot's active one; never closes an SSE — that's
 * the registry's job (`StreamRegistry.close`).
 */
export function markRunTerminal(
  slot: BackgroundSlot,
  runId: string,
  status: string,
): BackgroundSlot {
  if (slot.activeRunId !== runId) {
    return { ...slot, status };
  }
  const userMessageIdByRunId = new Map(slot.userMessageIdByRunId);
  userMessageIdByRunId.delete(runId);
  return {
    ...slot,
    activeRunId: null,
    userMessageIdByRunId,
    status,
  };
}

/**
 * Set of conversation_ids that have a non-null `activeRunId`. Drives
 * the sidebar live-pill set — replaces the singleton
 * `liveConversationId`.
 */
export function liveConversationIds(
  slots: ReadonlyMap<string, BackgroundSlot>,
): ReadonlySet<string> {
  const out = new Set<string>();
  for (const [convId, slot] of slots) {
    if (slot.activeRunId !== null) {
      out.add(convId);
    }
  }
  return out;
}

/**
 * LRU eviction: drop the heavyweight content (`items`, `citations`,
 * `sources`) of slots that haven't been visible recently AND have no
 * active run. Metadata stays so the live-set stays consistent.
 *
 * `protectedConvIds` includes the visible conv + any that have active
 * runs; never evicted.
 */
export function evictColdContent(
  slots: ReadonlyMap<string, BackgroundSlot>,
  cap: number,
  protectedConvIds: ReadonlySet<string>,
): Map<string, BackgroundSlot> {
  const next = new Map(slots);
  if (next.size <= cap) {
    return next;
  }
  const candidates = [...next.entries()]
    .filter(
      ([convId, slot]) =>
        slot.activeRunId === null && !protectedConvIds.has(convId),
    )
    .sort((a, b) => a[1].lastVisibleAt - b[1].lastVisibleAt);

  let toDrop = next.size - cap;
  for (const [convId] of candidates) {
    if (toDrop <= 0) {
      break;
    }
    next.delete(convId);
    toDrop -= 1;
  }
  return next;
}
