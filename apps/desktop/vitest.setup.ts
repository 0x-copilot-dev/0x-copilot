// Vitest setup for the desktop workspace.
//
// Node's experimental `--localstorage-file` global can shadow jsdom's
// `localStorage` with a misconfigured Storage (its `getItem` is not a
// function), so any renderer test that mounts a component reading
// `globalThis.localStorage` — e.g. the Run cockpit via `useRunMode`'s
// `LocalStorageKeyValueStore` — throws during render. Install a clean
// in-memory Storage when the ambient one is unusable, matching the real
// Electron (Chromium) renderer where `localStorage` works. Cleared per test
// so run-mode / KV state never leaks between cases.
import { beforeEach } from "vitest";

function createMemoryStorage(): Storage {
  const map = new Map<string, string>();
  const store = {
    get length(): number {
      return map.size;
    },
    clear(): void {
      map.clear();
    },
    getItem(key: string): string | null {
      return map.has(key) ? (map.get(key) as string) : null;
    },
    key(index: number): string | null {
      return [...map.keys()][index] ?? null;
    },
    removeItem(key: string): void {
      map.delete(key);
    },
    setItem(key: string, value: string): void {
      map.set(key, String(value));
    },
  };
  return store as unknown as Storage;
}

const ambient = (globalThis as { localStorage?: Storage }).localStorage;
if (!ambient || typeof ambient.getItem !== "function") {
  Object.defineProperty(globalThis, "localStorage", {
    value: createMemoryStorage(),
    configurable: true,
    writable: true,
  });
}

beforeEach(() => {
  try {
    globalThis.localStorage?.clear();
  } catch {
    /* no-op — some node test files run without a DOM-like global */
  }
});
