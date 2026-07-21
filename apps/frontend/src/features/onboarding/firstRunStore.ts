// Web first-run (FTUE) completion store — records, per web identity, whether the
// onboarding gate has been completed (setup finished, first run sent, or
// skipped). The desktop persists this in a main-process `first-run.json` over
// IPC; the web has no main process, so it persists through the shared
// substrate-agnostic `KeyValueStore` (localStorage-backed on web) — exactly the
// seam `firstRun.ts` documents ("web: a `KeyValueStore` namespaced by user id").
//
// Keyed by the web identity (org_id + user_id) so two accounts signed in on one
// browser profile each see their own first run. A missing key reads as "not
// completed" so onboarding shows — the safe default is to never skip onboarding
// on an unreadable/absent flag; the flag persists again on the next completion.
//
// This is a UX flag, NOT a secret — it belongs in `KeyValueStore` (product
// state), never `SecretStorage`. The value stored is the completion ISO
// timestamp (presence is the flag; the timestamp is informational only).

import type {
  FirstRunCompleteReason,
  FirstRunStore,
  KeyValueStore,
} from "@0x-copilot/chat-surface";

/** KeyValueStore namespace for the per-identity completion flag. */
const STORE_KEY_PREFIX = "enterprise.first-run.completed";

/** The web identity that namespaces the flag — org + user (from the app's
 *  authenticated identity / `UserProfileContext`). */
export interface WebFirstRunIdentity {
  readonly orgId: string;
  readonly userId: string;
}

/** The per-identity KeyValueStore key. Colon-joined so two accounts on one
 *  browser profile never collide. */
export function firstRunStoreKey(identity: WebFirstRunIdentity): string {
  return `${STORE_KEY_PREFIX}.${identity.orgId}:${identity.userId}`;
}

/**
 * The web `FirstRunStore`. Structurally satisfies the shared `FirstRunStore`
 * port (so it drops into any consumer typed against it), but narrows the reads
 * to SYNC — the web `KeyValueStore` is synchronous, so the gate can decide on
 * the first paint with no loading flash (unlike the desktop's async IPC read).
 */
export interface WebFirstRunStore extends FirstRunStore {
  /** `true` once onboarding has been completed for this identity. Synchronous —
   *  a bad/absent read is `false` (onboarding shows). */
  isComplete(): boolean;
  /** Persist completion for this identity. The `reason` is not gating-relevant
   *  (skip and finish both complete the gate); it is accepted for the shared
   *  port shape and kept for future auditing. */
  markComplete(reason: FirstRunCompleteReason): void;
}

/**
 * Build the web `FirstRunStore` bound to a `KeyValueStore` + web identity.
 *
 * A write failure is swallowed by `KeyValueStore` implementations (localStorage
 * can throw in private mode); the observable effect of a lost write is only that
 * onboarding may show once more on the next visit — never a trap or a crash.
 */
export function createWebFirstRunStore(
  store: KeyValueStore,
  identity: WebFirstRunIdentity,
): WebFirstRunStore {
  const key = firstRunStoreKey(identity);
  return {
    isComplete(): boolean {
      return store.get(key) !== null;
    },
    markComplete(_reason: FirstRunCompleteReason): void {
      store.set(key, new Date().toISOString());
    },
  };
}
