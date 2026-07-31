// Composer model memory — what the model pill opens on across mounts.
//
// The pill's popover header says "Model — this chat", but the selection was
// React state inside the host binder only. Leaving the Run destination (or
// switching chats) unmounts that binder, so the next mount recomputed the
// auto-default and the user's pick was silently gone. This is the memory behind
// the pill: substrate-free, backed by the `KeyValueStore` port, so every host
// persists it the same way.
//
// Two layers, answering two different questions:
//
//   * per-conversation — reopening a chat restores the model picked IN it,
//     which is exactly what the "this chat" header promises;
//   * last-used — a chat with no remembered pick (a brand-new one) opens on the
//     model chosen most recently anywhere, the sticky behaviour every chat
//     client has.
//
// Both live under ONE key. A conversation id has unbounded cardinality, so a
// key-per-chat namespace would grow forever; one document holding a capped,
// most-recent-first list prunes in the same write that records the pick.
//
// A remembered id is a HINT, never authority. Nothing here validates it — the
// caller resolves it against the live catalog and ignores it when the model is
// gone, curated out, or its provider key was removed.

import type { KeyValueStore } from "../ports";

/** The single `KeyValueStore` key holding the whole preference document. */
export const COMPOSER_MODEL_PREFERENCE_KEY =
  "copilot.composer.model-preference";

/**
 * How many per-conversation picks are retained (most-recent-first). A cap is
 * what keeps the document bounded; chats past it fall back to `last`, which is
 * the same model they would have opened on anyway in the common case.
 */
export const COMPOSER_MODEL_PREFERENCE_CHAT_LIMIT = 100;

/** `[conversationId, modelId]`, most-recent-first. */
type ChatPick = readonly [string, string];

interface StoredPreference {
  readonly last: string | null;
  readonly chats: readonly ChatPick[];
}

export interface ComposerModelPreference {
  /** The model id picked most recently in ANY chat; `null` when never picked. */
  lastUsed(): string | null;
  /** The model id picked in this conversation; `null` when it has no pick. */
  forConversation(conversationId: string | null | undefined): string | null;
  /**
   * Record an explicit user pick. Always updates `lastUsed`; also records it
   * against `conversationId` when there is one (a brand-new chat has no id yet
   * — its pick still carries over, via `lastUsed`, once the id exists).
   */
  remember(modelId: string, conversationId?: string | null): void;
}

export interface ComposerModelPreferenceOptions {
  /** Override the storage key (tests / a second, isolated composer surface). */
  readonly key?: string;
  /** Override the retained-chat cap (tests). */
  readonly chatLimit?: number;
}

export function createComposerModelPreference(
  store: KeyValueStore,
  options: ComposerModelPreferenceOptions = {},
): ComposerModelPreference {
  const key = options.key ?? COMPOSER_MODEL_PREFERENCE_KEY;
  const chatLimit = options.chatLimit ?? COMPOSER_MODEL_PREFERENCE_CHAT_LIMIT;

  const read = (): StoredPreference => parsePreference(store.get(key));

  return {
    lastUsed: () => read().last,

    forConversation: (conversationId) => {
      const id = normalizeId(conversationId);
      if (id === null) return null;
      const hit = read().chats.find(([chatId]) => chatId === id);
      return hit === undefined ? null : hit[1];
    },

    remember: (modelId, conversationId) => {
      if (modelId === "") return;
      const id = normalizeId(conversationId);
      const current = read();
      // Re-picking in a chat moves it to the head, so the cap evicts the
      // genuinely coldest chat rather than the oldest-first-picked one.
      const chats = current.chats.filter(([chatId]) => chatId !== id);
      const next: StoredPreference = {
        last: modelId,
        chats: (id === null
          ? chats
          : [[id, modelId] as ChatPick, ...chats]
        ).slice(0, chatLimit),
      };
      try {
        store.set(key, JSON.stringify(next));
      } catch {
        // A preference that cannot be persisted (quota, a locked store) must
        // never break the pick the user just made — the in-memory selection
        // still stands for this mount.
      }
    },
  };
}

function normalizeId(conversationId: string | null | undefined): string | null {
  return typeof conversationId === "string" && conversationId !== ""
    ? conversationId
    : null;
}

/**
 * Parse the stored document defensively. Local storage is user-writable and
 * survives across app versions, so anything unreadable degrades to "no
 * preference" rather than throwing inside a render or an event handler.
 */
function parsePreference(raw: string | null): StoredPreference {
  const empty: StoredPreference = { last: null, chats: [] };
  if (raw === null || raw === "") return empty;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return empty;
  }
  if (typeof parsed !== "object" || parsed === null) return empty;
  const doc = parsed as { last?: unknown; chats?: unknown };
  const last =
    typeof doc.last === "string" && doc.last !== "" ? doc.last : null;
  const chats = Array.isArray(doc.chats)
    ? doc.chats
        .filter(isChatPick)
        .map(([id, modelId]) => [id, modelId] as ChatPick)
    : [];
  return { last, chats };
}

function isChatPick(entry: unknown): entry is ChatPick {
  return (
    Array.isArray(entry) &&
    entry.length === 2 &&
    typeof entry[0] === "string" &&
    entry[0] !== "" &&
    typeof entry[1] === "string" &&
    entry[1] !== ""
  );
}
