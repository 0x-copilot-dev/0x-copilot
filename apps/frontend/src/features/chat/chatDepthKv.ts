// Per-conversation + per-user `reasoning_depth` persistence on top of
// the chat-surface `KeyValueStore` port.
//
// Source: chats-canvas-prd §16 (binding) — resolution order is:
//   1. per-conversation `chats.thread.<conversation_id>.reasoning_depth`
//   2. per-user `chats.default_depth`
//   3. `null` (the runtime default)
//
// The Composer's `topBarSlot` depth-picker (P1-B's Composer extras) is
// the eventual write source. P1-C ships the KV plumbing now so the wire
// path is end-to-end ready and tested; the picker UI lights up once
// P1-B's extras land. ChatScreen already persists depth in localStorage
// today — this helper layers KV on top via the substrate-agnostic port.
//
// Why a port, not direct localStorage: the desktop substrate has no
// browser localStorage. KeyValueStore is the host-provided port that
// backs onto `Memento` on desktop and `localStorage` on web. Single
// source of truth for product KV state.

import type { KeyValueStore } from "@enterprise-search/chat-surface";

import {
  DEFAULT_THINKING_DEPTH,
  isThinkingDepth,
  type ThinkingDepth,
} from "./depth";

const PER_CONVERSATION_KEY_PREFIX = "chats.thread.";
const PER_CONVERSATION_KEY_SUFFIX = ".reasoning_depth";
const PER_USER_DEFAULT_KEY = "chats.default_depth";

/**
 * Per-conversation KV key. Lives under the same `chats.thread.<id>.*`
 * namespace as the mode persistence in `packages/chat-surface/src/
 * thread-canvas/modePersistence.ts`. Single namespace, multiple suffixes.
 */
export function perConversationDepthKey(conversationId: string): string {
  return `${PER_CONVERSATION_KEY_PREFIX}${conversationId}${PER_CONVERSATION_KEY_SUFFIX}`;
}

/**
 * Per-user fallback. When a conversation has no explicit depth, the
 * user's `chats.default_depth` applies. Set when the user adjusts depth
 * outside a conversation context (e.g. a future Settings panel) or as a
 * cascade from the latest per-conversation choice.
 */
export const DEFAULT_DEPTH_KEY = PER_USER_DEFAULT_KEY;

/**
 * Read the effective depth for a given conversation, following the §16
 * resolution order. Returns `null` when neither key is set — callers
 * pass that through to `CreateRunRequest.reasoning_depth` so the runtime
 * applies its own default (no regression vs. pre-depth behaviour, per
 * api-types L1271).
 */
export function readDepth(
  store: KeyValueStore,
  conversationId: string | null,
): ThinkingDepth | null {
  if (conversationId !== null) {
    const perConv = store.get(perConversationDepthKey(conversationId));
    if (isThinkingDepth(perConv)) {
      return perConv;
    }
  }
  const perUser = store.get(PER_USER_DEFAULT_KEY);
  if (isThinkingDepth(perUser)) {
    return perUser;
  }
  return null;
}

/**
 * Read the effective depth with a hard fallback to "balanced" (the
 * cross-audit Q10 default). Used by call sites that need a non-null
 * `ThinkingDepth` value — e.g. the Composer's `initialDepth` prop.
 */
export function readDepthOrDefault(
  store: KeyValueStore,
  conversationId: string | null,
): ThinkingDepth {
  return readDepth(store, conversationId) ?? DEFAULT_THINKING_DEPTH;
}

/**
 * Persist the per-conversation depth. Passing `null` removes the key
 * (so subsequent reads fall back to per-user default). Mirrors the
 * `KeyValueStore.set(key, null)` removal contract.
 */
export function writeConversationDepth(
  store: KeyValueStore,
  conversationId: string,
  depth: ThinkingDepth | null,
): void {
  store.set(perConversationDepthKey(conversationId), depth);
}

/**
 * Persist the per-user default depth.
 */
export function writeDefaultDepth(
  store: KeyValueStore,
  depth: ThinkingDepth | null,
): void {
  store.set(PER_USER_DEFAULT_KEY, depth);
}
