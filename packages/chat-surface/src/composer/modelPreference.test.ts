import { describe, expect, it } from "vitest";

import type { KeyValueStore } from "../ports";
import {
  COMPOSER_MODEL_PREFERENCE_KEY,
  createComposerModelPreference,
} from "./modelPreference";

class MemoryStore implements KeyValueStore {
  readonly map = new Map<string, string>();

  get(key: string): string | null {
    return this.map.get(key) ?? null;
  }

  set(key: string, value: string | null): void {
    if (value === null) {
      this.map.delete(key);
      return;
    }
    this.map.set(key, value);
  }

  keys(prefix?: string): readonly string[] {
    return [...this.map.keys()].filter(
      (key) => prefix === undefined || key.startsWith(prefix),
    );
  }
}

describe("composer model preference", () => {
  it("remembers a pick per conversation and as the last-used model", () => {
    const store = new MemoryStore();
    const pref = createComposerModelPreference(store);

    expect(pref.lastUsed()).toBeNull();
    expect(pref.forConversation("chat-1")).toBeNull();

    pref.remember("claude-sonnet-5", "chat-1");

    expect(pref.forConversation("chat-1")).toBe("claude-sonnet-5");
    expect(pref.lastUsed()).toBe("claude-sonnet-5");
    // A chat with no pick of its own reads nothing — the caller falls back to
    // `lastUsed` itself, so the two layers stay distinguishable.
    expect(pref.forConversation("chat-2")).toBeNull();
  });

  it("keeps each conversation's pick independent", () => {
    const store = new MemoryStore();
    const pref = createComposerModelPreference(store);

    pref.remember("claude-opus-5", "chat-1");
    pref.remember("gpt-5.4-mini", "chat-2");

    expect(pref.forConversation("chat-1")).toBe("claude-opus-5");
    expect(pref.forConversation("chat-2")).toBe("gpt-5.4-mini");
    expect(pref.lastUsed()).toBe("gpt-5.4-mini");
  });

  it("records a pick with no conversation as last-used only (a brand-new chat)", () => {
    const store = new MemoryStore();
    const pref = createComposerModelPreference(store);

    pref.remember("claude-haiku-4-5", null);

    expect(pref.lastUsed()).toBe("claude-haiku-4-5");
    // Nothing was filed under a synthetic id that every new chat would share.
    expect(
      JSON.parse(store.get(COMPOSER_MODEL_PREFERENCE_KEY) ?? "{}"),
    ).toEqual({ last: "claude-haiku-4-5", chats: [] });
  });

  it("caps the retained chats, evicting the coldest", () => {
    const store = new MemoryStore();
    const pref = createComposerModelPreference(store, { chatLimit: 2 });

    pref.remember("m1", "chat-1");
    pref.remember("m2", "chat-2");
    // Re-picking in chat-1 moves it back to the head, so chat-2 is now coldest.
    pref.remember("m1b", "chat-1");
    pref.remember("m3", "chat-3");

    expect(pref.forConversation("chat-1")).toBe("m1b");
    expect(pref.forConversation("chat-3")).toBe("m3");
    expect(pref.forConversation("chat-2")).toBeNull();
  });

  it("degrades to no preference on a corrupt document rather than throwing", () => {
    const store = new MemoryStore();
    store.set(COMPOSER_MODEL_PREFERENCE_KEY, "{not json");
    const pref = createComposerModelPreference(store);

    expect(pref.lastUsed()).toBeNull();
    expect(pref.forConversation("chat-1")).toBeNull();

    // …and a write repairs it.
    pref.remember("gpt-5.4-mini", "chat-1");
    expect(pref.forConversation("chat-1")).toBe("gpt-5.4-mini");
  });

  it("drops malformed entries inside an otherwise readable document", () => {
    const store = new MemoryStore();
    store.set(
      COMPOSER_MODEL_PREFERENCE_KEY,
      JSON.stringify({
        last: 42,
        chats: [["chat-1"], "nope", ["chat-2", "gpt-5.4-mini"], [1, 2]],
      }),
    );
    const pref = createComposerModelPreference(store);

    expect(pref.lastUsed()).toBeNull();
    expect(pref.forConversation("chat-1")).toBeNull();
    expect(pref.forConversation("chat-2")).toBe("gpt-5.4-mini");
  });

  it("ignores an empty model id and an empty conversation id", () => {
    const store = new MemoryStore();
    const pref = createComposerModelPreference(store);

    pref.remember("", "chat-1");
    expect(pref.lastUsed()).toBeNull();

    pref.remember("gpt-5.4-mini", "");
    expect(pref.lastUsed()).toBe("gpt-5.4-mini");
    expect(pref.forConversation("")).toBeNull();
  });

  it("survives a store whose writes throw (quota) without losing the read path", () => {
    const store = new MemoryStore();
    const throwing: KeyValueStore = {
      get: (key) => store.get(key),
      set: () => {
        throw new Error("QuotaExceededError");
      },
      keys: (prefix) => store.keys(prefix),
    };
    const pref = createComposerModelPreference(throwing);

    expect(() => pref.remember("gpt-5.4-mini", "chat-1")).not.toThrow();
    expect(pref.lastUsed()).toBeNull();
  });
});
