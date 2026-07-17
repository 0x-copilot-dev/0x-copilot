import type { ConversationId, ItemRef } from "@0x-copilot/api-types";
import { afterEach, describe, expect, it } from "vitest";

import {
  ItemRefResolverAlreadyRegistered,
  ItemRefResolverNotRegistered,
  __resetItemRefRegistryForTests,
  hasItemRefResolver,
  registerItemRefResolver,
  resolveItemRef,
  unregisterItemRefResolver,
  type ItemRefResolved,
} from "./registry";

const A_LABEL: ItemRefResolved = {
  label: "Acme renewal",
  icon: "📝",
  route: { kind: "chat", conversationId: "conv_001" },
  breadcrumb: "Chats",
};

afterEach(() => {
  __resetItemRefRegistryForTests();
});

describe("ItemRef registry", () => {
  it("returns false from hasItemRefResolver when nothing is registered", () => {
    expect(hasItemRefResolver("chat")).toBe(false);
  });

  it("registers and resolves a ref by kind", async () => {
    registerItemRefResolver("chat", async (id) => {
      // Type-level: id is ConversationId here, not plain string.
      const _typed: ConversationId = id;
      void _typed;
      return A_LABEL;
    });
    expect(hasItemRefResolver("chat")).toBe(true);
    const ref: ItemRef = {
      kind: "chat",
      id: "conv_001" as ConversationId,
    };
    const resolved = await resolveItemRef(ref);
    expect(resolved).toEqual(A_LABEL);
  });

  it("rejects duplicate registration without replace: true", () => {
    registerItemRefResolver("chat", async () => A_LABEL);
    expect(() => registerItemRefResolver("chat", async () => A_LABEL)).toThrow(
      ItemRefResolverAlreadyRegistered,
    );
    try {
      registerItemRefResolver("chat", async () => A_LABEL);
    } catch (e) {
      expect((e as ItemRefResolverAlreadyRegistered).kind).toBe("chat");
    }
  });

  it("accepts duplicate registration with replace: true", async () => {
    registerItemRefResolver("chat", async () => A_LABEL);
    const replacement: ItemRefResolved = {
      label: "Replaced",
      icon: null,
      route: null,
    };
    registerItemRefResolver("chat", async () => replacement, { replace: true });
    const resolved = await resolveItemRef({
      kind: "chat",
      id: "conv_001" as ConversationId,
    });
    expect(resolved).toEqual(replacement);
  });

  it("resolveItemRef rejects with ItemRefResolverNotRegistered when no resolver is wired", async () => {
    await expect(
      resolveItemRef({ kind: "chat", id: "conv_001" as ConversationId }),
    ).rejects.toBeInstanceOf(ItemRefResolverNotRegistered);
  });

  it("unregister removes the resolver and returns whether one existed", () => {
    expect(unregisterItemRefResolver("chat")).toBe(false);
    registerItemRefResolver("chat", async () => A_LABEL);
    expect(unregisterItemRefResolver("chat")).toBe(true);
    expect(hasItemRefResolver("chat")).toBe(false);
  });

  it("isolates resolvers per kind", async () => {
    registerItemRefResolver("chat", async () => A_LABEL);
    expect(hasItemRefResolver("chat")).toBe(true);
    expect(hasItemRefResolver("todo")).toBe(false);
    await expect(
      resolveItemRef({
        kind: "todo",
        id: "todo_x" as ItemRef extends { kind: "todo"; id: infer I }
          ? I
          : never,
      }),
    ).rejects.toBeInstanceOf(ItemRefResolverNotRegistered);
  });

  it("propagates resolver-returned null as a deleted signal", async () => {
    registerItemRefResolver("chat", async () => null);
    const resolved = await resolveItemRef({
      kind: "chat",
      id: "conv_x" as ConversationId,
    });
    expect(resolved).toBeNull();
  });
});
