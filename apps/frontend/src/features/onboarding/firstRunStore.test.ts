// Web first-run store — completion flag over a KeyValueStore, namespaced by the
// web identity (org+user). Mirrors the desktop `first-run-store` unit coverage.

import { describe, expect, it } from "vitest";

import type { KeyValueStore } from "@0x-copilot/chat-surface";

import {
  createWebFirstRunStore,
  firstRunStoreKey,
  type WebFirstRunIdentity,
} from "./firstRunStore";

/** Minimal in-memory KeyValueStore (the web impl wraps localStorage; here a Map
 *  keeps the store logic under test without a DOM). */
function fakeStore(seed: Record<string, string> = {}): KeyValueStore & {
  readonly map: Map<string, string>;
} {
  const map = new Map<string, string>(Object.entries(seed));
  return {
    map,
    get: (key) => map.get(key) ?? null,
    set: (key, value) => {
      if (value === null) map.delete(key);
      else map.set(key, value);
    },
    keys: (prefix) =>
      [...map.keys()].filter((k) => prefix === undefined || k.startsWith(prefix)),
  };
}

const ACME: WebFirstRunIdentity = { orgId: "org_acme", userId: "user_sarah" };
const OTHER: WebFirstRunIdentity = { orgId: "org_acme", userId: "user_marcus" };

describe("firstRunStoreKey", () => {
  it("namespaces the key by org and user", () => {
    expect(firstRunStoreKey(ACME)).toBe(
      "enterprise.first-run.completed.org_acme:user_sarah",
    );
  });

  it("gives two users on one org distinct keys", () => {
    expect(firstRunStoreKey(ACME)).not.toBe(firstRunStoreKey(OTHER));
  });
});

describe("createWebFirstRunStore", () => {
  it("reads not-complete when the flag is absent", () => {
    const store = createWebFirstRunStore(fakeStore(), ACME);
    expect(store.isComplete()).toBe(false);
  });

  it("persists completion so isComplete flips true", () => {
    const kv = fakeStore();
    const store = createWebFirstRunStore(kv, ACME);
    store.markComplete("sent");
    expect(store.isComplete()).toBe(true);
    // The stored value is an ISO timestamp (presence is the flag; the value is
    // informational only).
    const stored = kv.get(firstRunStoreKey(ACME));
    expect(stored).not.toBeNull();
    expect(() => new Date(stored as string).toISOString()).not.toThrow();
    expect(new Date(stored as string).toISOString()).toBe(stored);
  });

  it("isolates completion per identity", () => {
    const kv = fakeStore();
    createWebFirstRunStore(kv, ACME).markComplete("sent");
    // A different user on the same org is still first-run.
    expect(createWebFirstRunStore(kv, OTHER).isComplete()).toBe(false);
    // The completing user stays complete.
    expect(createWebFirstRunStore(kv, ACME).isComplete()).toBe(true);
  });

  it("reads back an externally-seeded flag (persistence across sessions)", () => {
    const kv = fakeStore({
      [firstRunStoreKey(ACME)]: "2026-01-01T00:00:00.000Z",
    });
    expect(createWebFirstRunStore(kv, ACME).isComplete()).toBe(true);
  });
});
