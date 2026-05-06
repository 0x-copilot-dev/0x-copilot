// PR F3 — sidebar pin / unpin state.
//
// Backend's `UpdateConversationRequest` doesn't yet accept a `metadata`
// field, so we keep pinned state client-side in localStorage keyed by
// user_id. Pinned threads collapse into a Pinned group at the top of
// the sidebar via `groupConversations(..., pinnedIds)`.
//
// When the backend gains a typed `metadata.pinned` column we can swap
// this hook to a server-driven fetch with no consumer changes — the
// `togglePinned(id)` signature stays the same.

import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY_PREFIX = "atlas:pinned:";

function storageKey(userId: string | null): string | null {
  if (!userId) {
    return null;
  }
  return `${STORAGE_KEY_PREFIX}${userId}`;
}

function readPinned(userId: string | null): Set<string> {
  const key = storageKey(userId);
  if (!key || typeof window === "undefined") {
    return new Set();
  }
  const raw = window.localStorage.getItem(key);
  if (!raw) {
    return new Set();
  }
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return new Set();
    }
    return new Set(
      parsed.filter((value): value is string => typeof value === "string"),
    );
  } catch {
    return new Set();
  }
}

function writePinned(userId: string | null, pinned: ReadonlySet<string>): void {
  const key = storageKey(userId);
  if (!key || typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(key, JSON.stringify([...pinned]));
}

export interface UsePinnedConversationsResult {
  pinnedIds: ReadonlySet<string>;
  togglePinned: (conversationId: string, nextPinned: boolean) => void;
}

export function usePinnedConversations(
  userId: string | null,
): UsePinnedConversationsResult {
  const [pinnedIds, setPinnedIds] = useState<Set<string>>(() =>
    readPinned(userId),
  );

  // Re-read when the bearer's user changes (workspace switch).
  useEffect(() => {
    setPinnedIds(readPinned(userId));
  }, [userId]);

  const togglePinned = useCallback(
    (conversationId: string, nextPinned: boolean) => {
      setPinnedIds((current) => {
        const next = new Set(current);
        if (nextPinned) {
          next.add(conversationId);
        } else {
          next.delete(conversationId);
        }
        writePinned(userId, next);
        return next;
      });
    },
    [userId],
  );

  return { pinnedIds, togglePinned };
}
