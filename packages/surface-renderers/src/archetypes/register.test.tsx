import { afterEach, describe, expect, it } from "vitest";

import {
  clearRegistry,
  resolveAdapter,
  unregisterAdapter,
} from "@0x-copilot/chat-surface";

import { registerAll } from "..";
import {
  ARCHETYPE_ADAPTERS,
  boardAdapter,
  docAdapter,
  messageAdapter,
  recordAdapter,
  registerArchetypeAdapters,
  tableAdapter,
} from ".";

const SCHEMES = ["record", "table", "message", "doc", "board"] as const;

afterEach(() => {
  clearRegistry();
});

describe("registerArchetypeAdapters", () => {
  it("registers all five archetype schemes", () => {
    registerArchetypeAdapters();
    expect(resolveAdapter("record://a")).toBe(recordAdapter);
    expect(resolveAdapter("table://a")).toBe(tableAdapter);
    expect(resolveAdapter("message://a")).toBe(messageAdapter);
    expect(resolveAdapter("doc://a")).toBe(docAdapter);
    expect(resolveAdapter("board://a")).toBe(boardAdapter);
  });

  it("exposes exactly the five adapters in ARCHETYPE_ADAPTERS", () => {
    expect(ARCHETYPE_ADAPTERS).toHaveLength(5);
    expect(ARCHETYPE_ADAPTERS.map((a) => a.scheme)).toEqual([...SCHEMES]);
  });

  // AC5 — double-call replaces, does not duplicate (same-version replace).
  it("is idempotent: a double call leaves exactly one adapter per scheme", () => {
    registerArchetypeAdapters();
    registerArchetypeAdapters();

    for (const scheme of SCHEMES) {
      // Still resolvable after two registrations.
      expect(resolveAdapter(`${scheme}://x`)).not.toBeNull();
      // Removing the single v1 entry once must leave nothing — proving there
      // was no duplicate v1 entry in the bucket.
      unregisterAdapter(scheme, 1);
      expect(resolveAdapter(`${scheme}://x`)).toBeNull();
    }
  });
});

describe("registerAll wires the archetype adapters", () => {
  it("registers the archetype schemes alongside the tier-1 adapters", () => {
    registerAll();
    for (const scheme of SCHEMES) {
      const resolved = resolveAdapter(`${scheme}://x`);
      expect(resolved).not.toBeNull();
      expect(resolved?.scheme).toBe(scheme);
    }
    // Tier-1 adapters remain registered too.
    expect(resolveAdapter("email://draft-1")).not.toBeNull();
    expect(resolveAdapter("sf-opp://oppty-9")).not.toBeNull();
  });
});
