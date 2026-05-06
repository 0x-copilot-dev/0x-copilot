import type { Conversation } from "@enterprise-search/api-types";

/**
 * Sidebar grouping reducer (PR 2.2).
 *
 * Day-buckets the user's conversations into `Today / Yesterday / Earlier`
 * against the **user's local timezone** via `Intl.DateTimeFormat`. Within
 * the `Earlier` bucket, optional `folder` strings are surfaced as
 * named sub-groups (`Earlier · Launches`, `Earlier · Personal`). Folder-
 * less conversations group under a single `Earlier` heading.
 *
 * The function is intentionally pure: `now` is injected so tests can
 * drive timezone + DST scenarios without touching the system clock.
 *
 * It also drops soft-deleted rows (`deleted_at !== null`) by default
 * — the PR 1.6 list endpoint already filters them server-side, but the
 * reducer is defensive in case stale rows ride a cached response.
 */

export interface ConversationGroup {
  id: string;
  label: string;
  conversations: Conversation[];
}

const DAY_MS = 86_400_000;
const EMPTY_PINNED: ReadonlySet<string> = new Set();

export function groupConversations(
  conversations: readonly Conversation[],
  now: Date,
  pinnedIds: ReadonlySet<string> = EMPTY_PINNED,
): ConversationGroup[] {
  const fmtDate = new Intl.DateTimeFormat(undefined, { dateStyle: "short" });
  const todayKey = fmtDate.format(now);
  const yesterdayKey = fmtDate.format(new Date(now.getTime() - DAY_MS));

  const pinned: Conversation[] = [];
  const today: Conversation[] = [];
  const yesterday: Conversation[] = [];
  // Folder name → rows. `__none__` is a sentinel for the no-folder bucket.
  const earlierByFolder = new Map<string, Conversation[]>();

  // Stable input order: most-recently-updated first.
  const sorted = [...conversations]
    .filter((c) => !c.deleted_at)
    .sort(
      (a, b) =>
        new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
    );

  for (const conversation of sorted) {
    if (pinnedIds.has(conversation.conversation_id) || isPinned(conversation)) {
      pinned.push(conversation);
      continue;
    }
    const updated = new Date(conversation.updated_at);
    const key = fmtDate.format(updated);
    if (key === todayKey) {
      today.push(conversation);
      continue;
    }
    if (key === yesterdayKey) {
      yesterday.push(conversation);
      continue;
    }
    const folder = conversation.folder?.trim() || "__none__";
    let bucket = earlierByFolder.get(folder);
    if (!bucket) {
      bucket = [];
      earlierByFolder.set(folder, bucket);
    }
    bucket.push(conversation);
  }

  const groups: ConversationGroup[] = [];
  if (pinned.length > 0) {
    groups.push({ id: "pinned", label: "Pinned", conversations: pinned });
  }
  if (today.length > 0) {
    groups.push({ id: "today", label: "Today", conversations: today });
  }
  if (yesterday.length > 0) {
    groups.push({
      id: "yesterday",
      label: "Yesterday",
      conversations: yesterday,
    });
  }
  // Folders ordered alphabetically; the un-foldered bucket appears last.
  const folderNames = [...earlierByFolder.keys()]
    .filter((name) => name !== "__none__")
    .sort((a, b) => a.localeCompare(b));
  for (const folder of folderNames) {
    groups.push({
      id: `earlier:${folder}`,
      label: `Earlier · ${folder}`,
      conversations: earlierByFolder.get(folder) ?? [],
    });
  }
  const noFolder = earlierByFolder.get("__none__");
  if (noFolder && noFolder.length > 0) {
    groups.push({
      id: "earlier",
      label: "Earlier",
      conversations: noFolder,
    });
  }
  return groups;
}

/**
 * PR F3 — pin / unpin uses `metadata.pinned: true` on the conversation
 * row. JSONB metadata doesn't require a server-side schema migration;
 * the boolean is opaque to the backend until a future PR adds an
 * indexed column. The UI is the source of truth for the rendering
 * order; pinned threads collapse into a single Pinned group at the top.
 */
export function isPinned(conversation: Conversation): boolean {
  const flag = (conversation.metadata as { pinned?: unknown } | undefined)
    ?.pinned;
  return flag === true;
}
