// Chats per-row projection (PRD-03 — Move 1).
//
// `toChatArchiveRow` is the ONE place a `Conversation` (the
// `@0x-copilot/api-types` wire shape) becomes a `ChatArchiveRow` (what the
// `<ChatsArchive>` destination renders). It lived duplicated in both host
// binders; `packages/chat-surface/CLAUDE.md` even instructed hosts to duplicate
// it — and that instruction is exactly why web migrated to the first-class
// `conversation.pinned` / `.preview` / `.model` fields (PRD-H.4) while desktop
// kept reading `conversation.metadata.*`, which NOTHING writes. So desktop's
// Pinned section was always empty and previews/models never rendered.
//
// This is a PURE projection over api-types shapes — no `window`, no `fetch`, no
// navigation — so it belongs in the package, not duplicated per host. Both
// hosts now call this single function (CLAUDE.md amended accordingly).
//
// PER-ROW ONLY: bucketing a flat list into pinned/recent/archived is NOT here —
// PRD-09 D1 moves that into the SQL query, so a shared bucketer would be deleted
// two waves later. The hosts keep their own thin bucketing over this row.

import type {
  ChatArchiveRow,
  ChatArchiveStatus,
  Conversation,
  ConversationId,
} from "@0x-copilot/api-types";

/**
 * Collapse the conversation lifecycle + latest-run status into the four-value
 * archive chip taxonomy (running / done / paused / archived). Archived wins
 * over run status (an archived thread's last run is history); otherwise the
 * latest run projects the chip.
 */
function chatArchiveStatus(conversation: Conversation): ChatArchiveStatus {
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
 * Project one `Conversation` into a `ChatArchiveRow`.
 *
 * `pinned` / `preview` / `model` are read from the FIRST-CLASS conversation
 * fields (PRD-H.4) — a real `pinned` column plus read-time preview/model
 * projections. The earlier metadata-blob reads (the `pinned`/`preview`/`model`
 * keys) are gone: nothing ever wrote those keys.
 */
export function toChatArchiveRow(conversation: Conversation): ChatArchiveRow {
  const title = conversation.title?.trim();
  return {
    id: conversation.conversation_id as ConversationId,
    title: title !== undefined && title.length > 0 ? title : "New chat",
    status: chatArchiveStatus(conversation),
    preview: conversation.preview ?? "",
    model: conversation.model ?? "",
    updated_at: conversation.updated_at,
    pinned: conversation.pinned === true,
  };
}
