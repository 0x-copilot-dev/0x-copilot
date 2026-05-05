// PR 1.3 — frontend drafts registry.
//
// Per-conversation map of draft_id -> Draft. Built from
// `draft_updated` events (live during a run) and from
// `GET /v1/agent/conversations/{cid}/drafts` (post-run / on switch).
//
// Idempotent on (draft_id, version): a higher version always wins; equal-or-
// lower versions are dropped. Replay across SSE reconnects is deterministic
// because the server emits versions monotonically per draft_id.

import type {
  Draft,
  DraftSection,
  DraftStatus,
  DraftUpdatedPayload,
  RuntimeEventEnvelope,
} from "@enterprise-search/api-types";

export type DraftRegistryByConversation = ReadonlyMap<
  string,
  ReadonlyMap<string, Draft>
>;

const EMPTY_CONVERSATION_REGISTRY: ReadonlyMap<string, Draft> = new Map();

export function emptyDraftRegistry(): DraftRegistryByConversation {
  return new Map();
}

export function draftsForConversation(
  registry: DraftRegistryByConversation,
  conversationId: string | null | undefined,
): ReadonlyMap<string, Draft> {
  if (!conversationId) {
    return EMPTY_CONVERSATION_REGISTRY;
  }
  return registry.get(conversationId) ?? EMPTY_CONVERSATION_REGISTRY;
}

export function draftsByCreatedAt(
  drafts: ReadonlyMap<string, Draft>,
): readonly Draft[] {
  return [...drafts.values()].sort(
    (a, b) =>
      new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
  );
}

/** Replace the conversation slice with a fresh GET /drafts response. */
export function seedDrafts(
  registry: DraftRegistryByConversation,
  conversationId: string,
  drafts: readonly Draft[],
): DraftRegistryByConversation {
  const slice = new Map<string, Draft>();
  for (const draft of drafts) {
    slice.set(draft.draft_id, draft);
  }
  const next = new Map(registry);
  next.set(conversationId, slice);
  return next;
}

/** Apply a single Draft (e.g. PATCH/send/discard response) to the registry. */
export function upsertDraft(
  registry: DraftRegistryByConversation,
  draft: Draft,
): DraftRegistryByConversation {
  const slice = registry.get(draft.conversation_id);
  const existing = slice?.get(draft.draft_id);
  if (existing && existing.version >= draft.version) {
    return registry;
  }
  const nextSlice = new Map(slice);
  nextSlice.set(draft.draft_id, draft);
  const next = new Map(registry);
  next.set(draft.conversation_id, nextSlice);
  return next;
}

/**
 * Apply a `draft_updated` event to the registry.
 *
 * The envelope's `conversation_id` is the source of truth; the payload
 * itself is the projected `DraftUpdatedPayload`. Higher-version events
 * overwrite; older versions are no-ops (deterministic on SSE replay /
 * `?after_sequence=N` resume).
 */
export function applyDraftUpdatedEvent(
  registry: DraftRegistryByConversation,
  event: RuntimeEventEnvelope,
): DraftRegistryByConversation {
  if (event.event_type !== "draft_updated") {
    return registry;
  }
  if (!isDraftUpdatedPayload(event.payload)) {
    return registry;
  }
  const conversationId = event.conversation_id ?? null;
  if (conversationId === null) {
    return registry;
  }
  const slice = registry.get(conversationId);
  const existing = slice?.get(event.payload.draft_id);
  if (existing && existing.version >= event.payload.version) {
    return registry;
  }
  const draft = draftFromUpdatedEvent(event.payload, conversationId, event);
  const nextSlice = new Map(slice);
  nextSlice.set(draft.draft_id, draft);
  const next = new Map(registry);
  next.set(conversationId, nextSlice);
  return next;
}

function isDraftUpdatedPayload(
  payload: unknown,
): payload is DraftUpdatedPayload {
  if (!payload || typeof payload !== "object") {
    return false;
  }
  const record = payload as Record<string, unknown>;
  return (
    typeof record.draft_id === "string" &&
    typeof record.version === "number" &&
    typeof record.status === "string" &&
    typeof record.title === "string" &&
    Array.isArray(record.sections) &&
    Array.isArray(record.citation_ids)
  );
}

/**
 * Project the wire payload into the FE Draft view-model.
 *
 * The event payload has no full body text — it carries `sections` because
 * the server already parsed the markdown. We join sections back to a
 * Markdown string so the FE Draft tab can hand the same string to
 * Streamdown that the agent originally wrote. This is a reversible
 * projection for read-only render; PATCH callers hold their own
 * `content_text` and supply it directly.
 */
function draftFromUpdatedEvent(
  payload: DraftUpdatedPayload,
  conversationId: string,
  event: RuntimeEventEnvelope,
): Draft {
  return {
    draft_id: payload.draft_id,
    version: payload.version,
    conversation_id: conversationId,
    run_id: event.run_id ?? null,
    user_id: "",
    title: payload.title,
    content_text: contentFromSections(payload.sections),
    sections: payload.sections,
    target_connector: payload.target_connector,
    target_metadata: payload.target_metadata,
    citation_ids: payload.citation_ids,
    status: payload.status as DraftStatus,
    created_at: event.created_at ?? new Date().toISOString(),
  };
}

function contentFromSections(sections: readonly DraftSection[]): string {
  const parts: string[] = [];
  for (const section of sections) {
    if (section.heading) {
      parts.push(`# ${section.heading}`);
    }
    if (section.body) {
      parts.push(section.body);
    }
  }
  return parts.join("\n\n");
}
