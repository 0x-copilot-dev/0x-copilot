// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

import { migrateLegacyPins } from "./migrateLegacyPins";

const pinConversation =
  vi.fn<(id: string, pinned: boolean, identity: unknown) => Promise<unknown>>();

vi.mock("../../api/agentApi", () => ({
  pinConversation: (id: string, pinned: boolean, identity: unknown) =>
    pinConversation(id, pinned, identity),
}));

const identity = { org_id: "org", user_id: "user" } as never;

// The CI vitest env runs with `--localstorage-file` set to an invalid path,
// leaving `window.localStorage` a stub without a working setItem. Substitute an
// in-memory implementation per test (mirrors useDiscoverablePref.test).
function installInMemoryLocalStorage(): void {
  const store = new Map<string, string>();
  const stub: Storage = {
    get length() {
      return store.size;
    },
    clear() {
      store.clear();
    },
    getItem(key: string) {
      return store.has(key) ? (store.get(key) as string) : null;
    },
    key(index: number) {
      return Array.from(store.keys())[index] ?? null;
    },
    removeItem(key: string) {
      store.delete(key);
    },
    setItem(key: string, value: string) {
      store.set(key, value);
    },
  };
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: stub,
  });
}

describe("migrateLegacyPins", () => {
  beforeEach(() => {
    installInMemoryLocalStorage();
    pinConversation.mockReset();
    pinConversation.mockResolvedValue({});
  });

  it("POSTs one pin per stored id, then deletes the key and sets the migrated marker", async () => {
    window.localStorage.setItem(
      "atlas:pinned:user",
      JSON.stringify(["c1", "c2", "c3"]),
    );
    const count = await migrateLegacyPins("user", identity);

    expect(count).toBe(3);
    expect(pinConversation).toHaveBeenCalledTimes(3);
    expect(pinConversation).toHaveBeenCalledWith("c1", true, identity);
    expect(pinConversation).toHaveBeenCalledWith("c3", true, identity);
    // Source key deleted; migrated marker set.
    expect(window.localStorage.getItem("atlas:pinned:user")).toBeNull();
    expect(window.localStorage.getItem("atlas:pinned:user:migrated")).toBe("1");
  });

  it("is a no-op on a second run (idempotent via the migrated marker)", async () => {
    window.localStorage.setItem("atlas:pinned:user", JSON.stringify(["c1"]));
    await migrateLegacyPins("user", identity);
    pinConversation.mockClear();
    // Re-seeding the key must NOT re-migrate once the marker exists.
    window.localStorage.setItem("atlas:pinned:user", JSON.stringify(["c9"]));
    const count = await migrateLegacyPins("user", identity);
    expect(count).toBe(0);
    expect(pinConversation).not.toHaveBeenCalled();
  });

  it("bounds the replay to 50 ids", async () => {
    const ids = Array.from({ length: 80 }, (_, i) => `c${i}`);
    window.localStorage.setItem("atlas:pinned:user", JSON.stringify(ids));
    const count = await migrateLegacyPins("user", identity);
    expect(count).toBe(50);
    expect(pinConversation).toHaveBeenCalledTimes(50);
  });

  it("does nothing without a user id", async () => {
    const count = await migrateLegacyPins(null, identity);
    expect(count).toBe(0);
    expect(pinConversation).not.toHaveBeenCalled();
  });

  it("swallows a per-id failure and still writes the marker", async () => {
    window.localStorage.setItem(
      "atlas:pinned:user",
      JSON.stringify(["c1", "c2"]),
    );
    pinConversation.mockRejectedValueOnce(new Error("boom"));
    const count = await migrateLegacyPins("user", identity);
    expect(count).toBe(2);
    expect(window.localStorage.getItem("atlas:pinned:user:migrated")).toBe("1");
  });
});
