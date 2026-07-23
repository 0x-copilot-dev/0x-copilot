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
// Bucketing (FR-4.5): archived rows → `archived`; `pinned === true`
// (and not archived) → `pinned`; everything else → `recent`. Empty buckets
// stay empty arrays and the destination hides the empty sections.
//
// PRD-H.4 — `pinned` / `preview` / `model` are now first-class projected
// conversation-list fields (a real `pinned` column + read-time preview/model
// projections). This binder reads those directly; the earlier `metadata.*`
// reads were a §11 gap — nothing ever wrote them, so Pinned was always empty
// and preview/model never rendered.

import type {
  ChatArchiveRow,
  ChatsArchive,
  Conversation,
  ConversationId,
  SectionResult,
} from "@0x-copilot/api-types";
import { toChatArchiveRow } from "@0x-copilot/chat-surface";

import { listConversations, pinConversation } from "../../../api/agentApi";
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
    const row = toChatArchiveRow(conversation);
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

// PRD-03 Move 1 — the per-row projection (`Conversation → ChatArchiveRow`) is
// now the SHARED `toChatArchiveRow` in `@0x-copilot/chat-surface`, so web and
// desktop can no longer drift (desktop kept reading `metadata.*` while web
// migrated to the first-class fields). This binder keeps only the fetch + bucket
// layer; bucketing moves into the SQL query in PRD-09.

/**
 * PRD-H.4 — pin / unpin binder for the Chats archive. Delegates the HTTP
 * call to the canonical `agentApi` client (network-layer rule) and returns
 * the updated conversation so the destination can reconcile its buckets.
 * Never throws: a transport failure resolves to a `status: "error"`
 * `SectionResult` mirroring `fetchChatsArchive`.
 */
export async function setChatPinned(
  conversationId: ConversationId,
  pinned: boolean,
  identity: RequestIdentity,
): Promise<SectionResult<ChatArchiveRow>> {
  try {
    const updated = await pinConversation(conversationId, pinned, identity);
    return { status: "ok", data: toChatArchiveRow(updated) };
  } catch (error: unknown) {
    return {
      status: "error",
      error: errorMessage(error, "Could not update pin."),
    };
  }
}
