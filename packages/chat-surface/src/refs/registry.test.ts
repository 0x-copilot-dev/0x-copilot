import type { ConversationId, ItemKind, ItemRef } from "@0x-copilot/api-types";
import { afterEach, describe, expect, it } from "vitest";

import {
  ItemRouteAlreadyRegistered,
  ItemRouteNotRegistered,
  __resetItemRouteRegistryForTests,
  hasItemRoute,
  registerItemRoute,
  resolveItemRoute,
  unregisterItemRoute,
} from "./registry";

// Every member of `ItemKind` — the barrel-emptiness proof (DoD 16) enumerates
// this. Drift from `refs.ts`'s union is caught by the exhaustiveness check the
// consuming resolvers already carry.
const ALL_KINDS: ReadonlyArray<ItemKind> = [
  "chat",
  "run",
  "subagent",
  "tool_result",
  "todo",
  "inbox_item",
  "project",
  "library_file",
  "library_page",
  "library_dataset",
  "agent",
  "tool",
  "skill",
  "connector",
  "person",
  "memory",
  "routine",
  "approval",
  "meeting_external",
];

afterEach(() => {
  __resetItemRouteRegistryForTests();
});

describe("ItemRoute registry (route-only, synchronous)", () => {
  it("returns false from hasItemRoute when nothing is registered", () => {
    expect(hasItemRoute("chat")).toBe(false);
  });

  it("registers and resolves a HOST route by kind, synchronously", () => {
    registerItemRoute("chat", (id) => ({ screen: "chat", conversationId: id }));
    expect(hasItemRoute("chat")).toBe(true);
    const ref: ItemRef = { kind: "chat", id: "conv_001" as ConversationId };
    // Synchronous — no promise, no effect.
    expect(resolveItemRoute(ref)).toEqual({
      screen: "chat",
      conversationId: "conv_001",
    });
  });

  it("rejects duplicate registration without replace: true", () => {
    registerItemRoute("chat", () => null);
    expect(() => registerItemRoute("chat", () => null)).toThrow(
      ItemRouteAlreadyRegistered,
    );
    try {
      registerItemRoute("chat", () => null);
    } catch (e) {
      expect((e as ItemRouteAlreadyRegistered).kind).toBe("chat");
    }
  });

  it("accepts duplicate registration with replace: true", () => {
    registerItemRoute("chat", () => ({ screen: "old" }));
    registerItemRoute("chat", () => ({ screen: "new" }), { replace: true });
    expect(
      resolveItemRoute({ kind: "chat", id: "conv_001" as ConversationId }),
    ).toEqual({ screen: "new" });
  });

  it("resolveItemRoute throws ItemRouteNotRegistered when no resolver is wired", () => {
    expect(() =>
      resolveItemRoute({ kind: "chat", id: "conv_001" as ConversationId }),
    ).toThrow(ItemRouteNotRegistered);
  });

  it("a resolver may return null (no route for this id)", () => {
    registerItemRoute("chat", () => null);
    expect(
      resolveItemRoute({ kind: "chat", id: "conv_x" as ConversationId }),
    ).toBeNull();
  });

  it("unregister removes the resolver and returns whether one existed", () => {
    expect(unregisterItemRoute("chat")).toBe(false);
    registerItemRoute("chat", () => null);
    expect(unregisterItemRoute("chat")).toBe(true);
    expect(hasItemRoute("chat")).toBe(false);
  });
});

// ===========================================================================
// DoD 16 — the package registers NO routes on import. Registration is now
// host-only (apps/frontend/src/app/itemRoutes.ts, apps/desktop/renderer/
// itemRoutes.ts); importing the barrel must leave the registry empty.
// ===========================================================================

describe("barrel import registers no routes (PRD-04 Seam B)", () => {
  it("hasItemRoute is false for every ItemKind after importing the package barrel", async () => {
    // Import ONLY the barrel — nothing else. If any destination still
    // registered a route at import time, one of these would be true.
    // (Generous timeout: transforming the whole barrel can be slow under
    // full-suite CPU contention; the assertion itself is instant.)
    await import("@0x-copilot/chat-surface");
    for (const kind of ALL_KINDS) {
      expect(hasItemRoute(kind)).toBe(false);
    }
  }, 30_000);
});
