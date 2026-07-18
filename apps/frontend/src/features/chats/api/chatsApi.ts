// chatsApi — Chats archive binder (desktop redesign, Phase 4 · PR-4.3).
//
// Source: docs/plan/desktop-redesign/phase-4/PRD.md FR-4.5 / FR-4.9 +
// §11 (the `/v1/chats/projects` stub is retired; the archive binds to
// `/v1/agent/conversations`, including archived).
//
// This is the host-side BINDER for the pure-presentation `<ChatsArchive>`
// component in `@0x-copilot/chat-surface`. It does NOT own an HTTP call —
// the `/v1/agent/conversations` request lives in the canonical
// `src/api/agentApi.ts` client (`listConversations`, per the frontend
// network-layer rule). This module's single job is to project the raw
// conversation list into the bucketed `ChatsArchive` read model the
// destination consumes, wrapped in the uniform `SectionResult` envelope
// (ok / error) that Inbox / Projects / Connectors already use.
//
// Bucketing (FR-4.5): archived rows → `archived`; `metadata.pinned === true`
// (and not archived) → `pinned`; everything else → `recent`. Empty buckets
// stay empty arrays and the destination hides the empty sections.

import type {
  ChatArchiveRow,
  ChatArchiveStatus,
  ChatsArchive,
  Conversation,
  ConversationId,
  SectionResult,
} from "@0x-copilot/api-types";

import { listConversations } from "../../../api/agentApi";
import type { RequestIdentity } from "../../../api/config";
import { errorMessage } from "../../../utils/errors";

/**
 * Fetch depth for the archive. The archive is a browse-and-reopen surface,
 * not an infinite feed, so a single generous page is enough for v1; a
 * dedicated paginated/bucketed endpoint is a §11 backend follow-up.
 */
const DEFAULT_LIMIT = 100;

/**
 * Load the conversation archive and project it into the bucketed shape the
 * `<ChatsArchive>` destination renders. Never throws: a transport failure
 * resolves to a `status: "error"` `SectionResult` so the destination shows
 * its Retry empty-state (FR-4.2) rather than bubbling an exception into the
 * route.
 */
export async function fetchChatsArchive(
  identity: RequestIdentity,
  options: { readonly limit?: number } = {},
): Promise<SectionResult<ChatsArchive>> {
  try {
    const response = await listConversations(identity, {
      limit: options.limit ?? DEFAULT_LIMIT,
      // FR-4.9 — the Archived section needs archived rows, so ask for them.
      includeArchived: true,
    });
    return { status: "ok", data: bucketConversations(response.conversations) };
  } catch (error: unknown) {
    return {
      status: "error",
      error: errorMessage(error, "Could not load chats."),
    };
  }
}

/**
 * Bucket a flat conversation list into pinned / recent / archived, dropping
 * soft-deleted rows. Pure + exported so the projection is unit-testable
 * without a mounted route.
 */
export function bucketConversations(
  conversations: ReadonlyArray<Conversation>,
): ChatsArchive {
  const pinned: ChatArchiveRow[] = [];
  const recent: ChatArchiveRow[] = [];
  const archived: ChatArchiveRow[] = [];

  for (const conversation of conversations) {
    // Soft-deleted rows are tombstones awaiting the retention sweeper — they
    // must never surface in the archive.
    if (conversation.deleted_at != null) {
      continue;
    }
    const row = toArchiveRow(conversation);
    if (row.status === "archived") {
      archived.push(row);
    } else if (row.pinned) {
      pinned.push(row);
    } else {
      recent.push(row);
    }
  }

  return { pinned, recent, archived };
}

/** Project one `Conversation` into a `ChatArchiveRow` (FR-4.6). */
export function toArchiveRow(conversation: Conversation): ChatArchiveRow {
  return {
    id: conversation.conversation_id as ConversationId,
    title: titleOf(conversation),
    status: statusOf(conversation),
    preview: previewOf(conversation),
    model: modelOf(conversation),
    updated_at: conversation.updated_at,
    pinned: isPinned(conversation),
  };
}

// ---------------------------------------------------------------------------
// Field projections
// ---------------------------------------------------------------------------

function titleOf(conversation: Conversation): string {
  const title = conversation.title?.trim();
  return title !== undefined && title.length > 0 ? title : "New chat";
}

/**
 * Collapse the conversation lifecycle + latest-run status into the
 * four-value archive chip taxonomy (running / done / paused / archived).
 *
 * Archived wins over run status (an archived thread's last run is history).
 * Otherwise the latest run projects the chip: in-flight → running,
 * awaiting a decision → paused, everything terminal (or never-run) → done.
 */
function statusOf(conversation: Conversation): ChatArchiveStatus {
  if (conversation.status === "archived" || conversation.archived_at != null) {
    return "archived";
  }
  switch (conversation.latest_run_status) {
    case "running":
    case "queued":
    case "cancelling":
      return "running";
    case "waiting_for_approval":
      return "paused";
    default:
      // completed / failed / cancelled / timed_out / null → no live work.
      return "done";
  }
}

/**
 * Pinned is a client-facing archive concept the conversation row does not
 * yet carry as a first-class column, so we read it from `metadata.pinned`.
 * Absent/falsey → not pinned; the row falls to the Recent bucket.
 */
function isPinned(conversation: Conversation): boolean {
  const metadata = conversation.metadata as { readonly pinned?: unknown };
  return metadata?.pinned === true;
}

/**
 * One-line preview snippet. `/v1/agent/conversations` does not yet project a
 * last-turn snippet (PRD §11 gap), so we surface `metadata.preview` when the
 * server supplies it and otherwise render an empty preview — the destination
 * simply shows the title + chip + time in that case.
 */
function previewOf(conversation: Conversation): string {
  const metadata = conversation.metadata as { readonly preview?: unknown };
  return typeof metadata?.preview === "string" ? metadata.preview : "";
}

/**
 * Mono model tag. Not a first-class conversation column either; best-effort
 * from `metadata.model`. Empty string tells the row to hide the model tag.
 */
function modelOf(conversation: Conversation): string {
  const metadata = conversation.metadata as { readonly model?: unknown };
  return typeof metadata?.model === "string" ? metadata.model : "";
}
