// Substrate-agnostic storage for SECRET values — bearer tokens, refresh
// tokens, anything an attacker with browser inspection should not see.
//
// Same shape as KeyValueStore (sync get/set/keys, null-on-absent),
// intentionally a DISTINCT type. The type boundary is the enforcement:
// code that holds a KeyValueStore handle cannot accidentally store a
// secret; code that holds a SecretStorage handle cannot accidentally
// stash non-secret pref state. Mistakes get caught at typecheck time,
// not at runtime when a token shows up in plaintext localStorage that
// shouldn't have been there.
//
// Web reference implementation (`WebSecretStorage`) is backed by
// `globalThis.localStorage` for now. That is a DEVELOPMENT-GRADE
// stand-in: anyone with devtools open can read the value. A real
// production deployment should move bearers to HttpOnly cookies with
// server-side rotation, or — once the desktop substrate ships — the
// VS Code extension's `SecretStorage` API (OS keychain on macOS /
// Windows). Keeping the substrate-portable contract here means the
// production swap is a one-line provider change at the App root, not
// a churn across every caller.

export interface SecretStorage {
  /** Returns the stored secret, or `null` when no value is set. */
  get(key: string): string | null;

  /** Stores a secret; passing `null` removes the key. */
  set(key: string, value: string | null): void;

  /**
   * Enumerates stored keys, optionally filtered by prefix. Used by
   * tests; production callers should know the exact keys they own.
   */
  keys(prefix?: string): readonly string[];
}

export interface WebSecretStorageConfig {
  /**
   * Override for tests. Defaults to `globalThis.localStorage`. The
   * implementation reads through the override on every call so test-time
   * stubs (`vi.stubGlobal("localStorage", …)`) land — same deferred-
   * lookup pattern as LocalStorageKeyValueStore.
   */
  readonly storage?: Storage;
}

/**
 * Web reference implementation. Wraps `globalThis.localStorage` with a
 * defensive try/catch so private-mode browsers (where storage throws on
 * access) degrade gracefully to "no persistence" rather than crashing
 * the auth flow.
 */
export class WebSecretStorage implements SecretStorage {
  readonly #override: Storage | undefined;

  constructor(config: WebSecretStorageConfig = {}) {
    this.#override = config.storage;
  }

  get(key: string): string | null {
    try {
      return this.#storage()?.getItem(key) ?? null;
    } catch {
      return null;
    }
  }

  set(key: string, value: string | null): void {
    try {
      const storage = this.#storage();
      if (!storage) {
        return;
      }
      if (value === null) {
        storage.removeItem(key);
        return;
      }
      storage.setItem(key, value);
    } catch {
      // Private-mode quota errors, opaque-origin storage, etc.
      // Auth flow keeps working off the in-memory bearer for this tab.
    }
  }

  keys(prefix?: string): readonly string[] {
    const out: string[] = [];
    try {
      const storage = this.#storage();
      if (!storage) {
        return out;
      }
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
    } catch {
      /* see get */
    }
    return out;
  }

  #storage(): Storage | undefined {
    if (this.#override) {
      return this.#override;
    }
    // Deferred lookup so test-time `vi.stubGlobal("localStorage", …)` or
    // jsdom navigations are picked up after construction.
    return globalThis.localStorage;
  }
}
