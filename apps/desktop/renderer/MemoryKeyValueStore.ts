import type { KeyValueStore } from "@enterprise-search/chat-surface";

// Phase 1-A placeholder. Phase 5 replaces this with an IPC-backed adapter
// that persists user preferences via the main process (alongside the
// secret-storage compartmentalization design, PRD D24 / §6.7).
//
// Renderer-side localStorage is deliberately avoided so that user state
// does not collect in Chromium's per-app store — by Phase 5 we want every
// persistent renderer state to flow through main.
export class MemoryKeyValueStore implements KeyValueStore {
  readonly #data = new Map<string, string>();

  get(key: string): string | null {
    return this.#data.get(key) ?? null;
  }

  set(key: string, value: string | null): void {
    if (value === null) {
      this.#data.delete(key);
      return;
    }
    this.#data.set(key, value);
  }

  keys(prefix?: string): readonly string[] {
    const out: string[] = [];
    for (const key of this.#data.keys()) {
      if (prefix !== undefined && !key.startsWith(prefix)) continue;
      out.push(key);
    }
    return out;
  }
}
