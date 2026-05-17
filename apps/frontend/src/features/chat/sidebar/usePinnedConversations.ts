// PR F3 — sidebar pin / unpin state.
//
// Backend's `UpdateConversationRequest` doesn't yet accept a `metadata`
// field, so we keep pinned state client-side keyed by user_id. Pinned
// threads collapse into a Pinned group at the top of the sidebar via
// `groupConversations(..., pinnedIds)`.
//
// Persistence routes through `KeyValueStore` (the substrate-agnostic
// port in @enterprise-search/chat-surface) — on web that's
// `LocalStorageKeyValueStore`; on desktop the extension host backs the
// same interface. No window.localStorage references live here.
//
// When the backend gains a typed `metadata.pinned` column we can swap
// this hook to a server-driven fetch with no consumer changes — the
// `togglePinned(id)` signature stays the same.

import {
  useKeyValueStore,
  type KeyValueStore,
} from "@enterprise-search/chat-surface";
import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY_PREFIX = "atlas:pinned:";

function storageKey(userId: string | null): string | null {
  if (!userId) {
    return null;
  }
  return `${STORAGE_KEY_PREFIX}${userId}`;
}

function readPinned(store: KeyValueStore, userId: string | null): Set<string> {
  const key = storageKey(userId);
  if (!key) {
    return new Set();
  }
  const raw = store.get(key);
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

function writePinned(
  store: KeyValueStore,
  userId: string | null,
  pinned: ReadonlySet<string>,
): void {
  const key = storageKey(userId);
  if (!key) {
    return;
  }
  store.set(key, JSON.stringify([...pinned]));
}

export interface UsePinnedConversationsResult {
  pinnedIds: ReadonlySet<string>;
  togglePinned: (conversationId: string, nextPinned: boolean) => void;
}

export function usePinnedConversations(
  userId: string | null,
): UsePinnedConversationsResult {
  const store = useKeyValueStore();
  const [pinnedIds, setPinnedIds] = useState<Set<string>>(() =>
    readPinned(store, userId),
  );

  // Re-read when the bearer's user changes (workspace switch). The store
  // itself is a stable singleton from the ChatShell provider, so it can
  // also be in the dep array without churn.
  useEffect(() => {
    setPinnedIds(readPinned(store, userId));
  }, [store, userId]);

  const togglePinned = useCallback(
    (conversationId: string, nextPinned: boolean) => {
      setPinnedIds((current) => {
        const next = new Set(current);
        if (nextPinned) {
          next.add(conversationId);
        } else {
          next.delete(conversationId);
        }
        writePinned(store, userId, next);
        return next;
      });
    },
    [store, userId],
  );

  return { pinnedIds, togglePinned };
}
