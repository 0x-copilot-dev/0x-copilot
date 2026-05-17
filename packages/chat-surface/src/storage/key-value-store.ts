// Substrate-agnostic key-value persistence for non-secret chat-surface
// state (pinned conversations, user preferences, layout choices, …).
//
// Why a port, not direct localStorage: the desktop substrate has no
// browser localStorage. It backs this interface with the VS Code
// extension's `Memento` (workspace/global), routed via the same
// extension-RPC bridge as the Transport. Components that opt into the
// store work in both substrates without conditional code.
//
// Why sync, not async: every value we put here is small and cacheable.
// The desktop impl will preload at extension activation and expose a
// sync surface backed by an in-memory cache, with writes scheduled in
// the background via RPC. Async would force every consumer to add
// loading states for what is effectively local data.
//
// What does NOT go here: bearer tokens, refresh tokens, any secret.
// Those live in the OS keychain via `SecretStorage` (apps/frontend's
// dev impl uses localStorage; the desktop impl will use the extension's
// `SecretStorage` API). The KeyValueStore is for product state.

export interface KeyValueStore {
  /** Returns the stored string, or `null` when no value is set. */
  get(key: string): string | null;

  /** Stores a string; passing `null` removes the key. */
  set(key: string, value: string | null): void;

  /**
   * Enumerates stored keys, optionally filtered by prefix. Used by
   * one-time migrations from legacy key namespaces. Implementations are
   * free to return keys in any order.
   */
  keys(prefix?: string): readonly string[];
}

export interface LocalStorageKeyValueStoreConfig {
  /**
   * Override for tests. Defaults to `window.localStorage`. The
   * implementation reads through the override on every call (matching
   * WebTransport's deferred-fetch pattern) so test-time stubs land.
   */
  readonly storage?: Storage;
}

/**
 * Web-substrate implementation. Wraps `window.localStorage` with no
 * value mutation beyond the substrate API. Same-origin only; if a
 * future iframe-embedded surface needs cross-origin storage, write a
 * separate impl rather than parameterizing this one.
 */
export class LocalStorageKeyValueStore implements KeyValueStore {
  readonly #override: Storage | undefined;

  constructor(config: LocalStorageKeyValueStoreConfig = {}) {
    this.#override = config.storage;
  }

  get(key: string): string | null {
    return this.#storage().getItem(key);
  }

  set(key: string, value: string | null): void {
    const storage = this.#storage();
    if (value === null) {
      storage.removeItem(key);
      return;
    }
    storage.setItem(key, value);
  }

  keys(prefix?: string): readonly string[] {
    const storage = this.#storage();
    const out: string[] = [];
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index);
      if (key === null) {
        continue;
      }
      if (prefix !== undefined && !key.startsWith(prefix)) {
        continue;
      }
      out.push(key);
    }
    return out;
  }

  #storage(): Storage {
    if (this.#override) {
      return this.#override;
    }
    // Deferred lookup so test-time `vi.stubGlobal("localStorage", …)` or
    // jsdom navigations are picked up after construction.
    return globalThis.localStorage;
  }
}
