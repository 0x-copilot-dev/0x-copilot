// migrateLegacyPins — one-shot, bounded migration of the retired localStorage
// pin concept onto the real server pin endpoint (PRD-09 D2).
//
// Before PRD-09 the ONLY pin affordance was the legacy ChatScreen sidebar, which
// wrote a JSON array of conversation ids to `atlas:pinned:<userId>` in
// localStorage and never touched the server. PRD-09 retires that concept in
// favour of the first-class `pinned` column. So nobody loses their pins, on the
// first mount after upgrade we replay the stored ids to
// `POST /v1/agent/conversations/{id}/pin` (idempotent server-side), then delete
// the key and set a `:migrated` marker so it never runs again.
//
// Best-effort and fire-and-forget: it has no UI, bounds the replay to 50 ids,
// and swallows per-id failures (a pin that can't be written is not worth
// blocking the surface for). This is the ONLY file in `apps`/`packages` allowed
// to name the legacy `atlas:pinned` key.

import type { RequestIdentity } from "../../api/config";
import { pinConversation } from "../../api/agentApi";

const STORAGE_KEY_PREFIX = "atlas:pinned:";
const MIGRATED_SUFFIX = ":migrated";
const MAX_MIGRATED_IDS = 50;

function storageKey(userId: string): string {
  return `${STORAGE_KEY_PREFIX}${userId}`;
}

function migratedKey(userId: string): string {
  return `${STORAGE_KEY_PREFIX}${userId}${MIGRATED_SUFFIX}`;
}

function readLegacyPinnedIds(userId: string): string[] {
  try {
    const raw = window.localStorage.getItem(storageKey(userId));
    if (raw === null) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((id): id is string => typeof id === "string");
  } catch {
    return [];
  }
}

/**
 * Replay the caller's legacy localStorage pins onto the server exactly once.
 *
 * Idempotent overall: after a successful pass the source key is deleted and a
 * `:migrated` marker is written, so subsequent mounts short-circuit. Returns the
 * number of ids it attempted to migrate (0 when already migrated or nothing was
 * stored) — for tests; callers ignore it (fire-and-forget).
 */
export async function migrateLegacyPins(
  userId: string | null,
  identity: RequestIdentity,
): Promise<number> {
  if (userId === null || userId === "") return 0;
  let store: Storage;
  try {
    store = window.localStorage;
    // Already migrated → never run again. Reading the marker also probes that
    // the environment has a working Storage (some test/SSR envs do not).
    if (store.getItem(migratedKey(userId)) !== null) return 0;
  } catch {
    return 0;
  }

  const ids = readLegacyPinnedIds(userId).slice(0, MAX_MIGRATED_IDS);
  // Even with no ids we drop the marker so we don't re-scan every mount.
  for (const id of ids) {
    try {
      await pinConversation(id, true, identity);
    } catch {
      // Best-effort: a single failed pin does not abort the migration.
    }
  }
  try {
    store.removeItem(storageKey(userId));
    store.setItem(migratedKey(userId), "1");
  } catch {
    // If we can't persist the marker the worst case is a redundant, still
    // idempotent, re-run next mount — harmless.
  }
  return ids.length;
}
