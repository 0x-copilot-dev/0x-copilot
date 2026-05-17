import type { KeyValueStore } from "@enterprise-search/chat-surface";
import { describe, expect, it } from "vitest";

import {
  DEFAULT_DEPTH_KEY,
  perConversationDepthKey,
  readDepth,
  readDepthOrDefault,
  writeConversationDepth,
  writeDefaultDepth,
} from "./chatDepthKv";

function makeStore(initial: Record<string, string> = {}): KeyValueStore {
  const data = new Map<string, string>(Object.entries(initial));
  return {
    get: (k) => data.get(k) ?? null,
    set: (k, v) => {
      if (v === null) {
        data.delete(k);
      } else {
        data.set(k, v);
      }
    },
    keys: (prefix) =>
      Array.from(data.keys()).filter((k) =>
        prefix === undefined ? true : k.startsWith(prefix),
      ),
  };
}

describe("chatDepthKv", () => {
  it("perConversationDepthKey scopes under chats.thread.<id>.*", () => {
    expect(perConversationDepthKey("conv_001")).toBe(
      "chats.thread.conv_001.reasoning_depth",
    );
  });

  it("readDepth prefers per-conversation over per-user default", () => {
    const store = makeStore({
      "chats.thread.conv_001.reasoning_depth": "fast",
      [DEFAULT_DEPTH_KEY]: "deep",
    });
    expect(readDepth(store, "conv_001")).toBe("fast");
  });

  it("readDepth falls back to per-user default when conversation unset", () => {
    const store = makeStore({ [DEFAULT_DEPTH_KEY]: "deep" });
    expect(readDepth(store, "conv_001")).toBe("deep");
  });

  it("readDepth returns null when neither key is set", () => {
    const store = makeStore();
    expect(readDepth(store, "conv_001")).toBeNull();
  });

  it("readDepth ignores invalid stored values", () => {
    const store = makeStore({
      "chats.thread.conv_001.reasoning_depth": "garbage",
      [DEFAULT_DEPTH_KEY]: "also-garbage",
    });
    expect(readDepth(store, "conv_001")).toBeNull();
  });

  it("readDepthOrDefault returns 'balanced' when nothing is set (cross-audit Q10)", () => {
    expect(readDepthOrDefault(makeStore(), "conv_001")).toBe("balanced");
  });

  it("writeConversationDepth + readDepth round-trip", () => {
    const store = makeStore();
    writeConversationDepth(store, "conv_001", "deep");
    expect(readDepth(store, "conv_001")).toBe("deep");
  });

  it("writeConversationDepth(null) removes the per-conv value", () => {
    const store = makeStore({
      "chats.thread.conv_001.reasoning_depth": "deep",
      [DEFAULT_DEPTH_KEY]: "fast",
    });
    writeConversationDepth(store, "conv_001", null);
    expect(readDepth(store, "conv_001")).toBe("fast");
  });

  it("writeDefaultDepth persists under the per-user key", () => {
    const store = makeStore();
    writeDefaultDepth(store, "balanced");
    expect(store.get(DEFAULT_DEPTH_KEY)).toBe("balanced");
  });

  it("readDepth without an active conversation falls back to per-user default", () => {
    const store = makeStore({ [DEFAULT_DEPTH_KEY]: "deep" });
    expect(readDepth(store, null)).toBe("deep");
  });
});
