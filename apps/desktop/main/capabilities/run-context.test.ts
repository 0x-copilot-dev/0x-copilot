// @vitest-environment node
import { describe, expect, it } from "vitest";

import { RunContextStore } from "./run-context";
import type { Grant, GrantSnapshot } from "./types";

function grant(overrides: Partial<Grant> = {}): Grant {
  return {
    grantId: "g1",
    root: "/data/private",
    mode: "read_write",
    label: "private",
    status: "active",
    createdAt: 1,
    updatedAt: 1,
    ...overrides,
  };
}

function snapshot(grants: Grant[] = [grant()]): GrantSnapshot {
  return { snapshotId: "snap-1", capturedAt: 100, grants };
}

describe("RunContextStore", () => {
  it("mints an opaque, unguessable, prefixed run-context id", () => {
    const store = new RunContextStore();
    const ctx = store.mint(snapshot());
    expect(ctx.runContext).toMatch(/^rcx_[A-Za-z0-9_-]{43}$/u); // 256-bit base64url
    expect(ctx.snapshotId).toBe("snap-1");
    expect(ctx.grants.map((g) => g.grantId)).toEqual(["g1"]);
  });

  it("mints a DISTINCT id on every call", () => {
    const store = new RunContextStore();
    const ids = new Set(
      Array.from({ length: 50 }, () => store.mint(snapshot()).runContext),
    );
    expect(ids.size).toBe(50);
  });

  it("uses the injected clock for capturedAt", () => {
    const store = new RunContextStore({ clock: () => 777 });
    expect(store.mint(snapshot()).capturedAt).toBe(777);
  });

  it("returns the pinned context by id, or null when unknown", () => {
    const store = new RunContextStore();
    const ctx = store.mint(snapshot());
    expect(store.get(ctx.runContext)?.grants[0].grantId).toBe("g1");
    expect(store.get("rcx_nope")).toBeNull();
  });

  it("freezes the pinned context and each grant (immutable)", () => {
    const store = new RunContextStore();
    const ctx = store.mint(snapshot());
    expect(Object.isFrozen(ctx)).toBe(true);
    expect(Object.isFrozen(ctx.grants)).toBe(true);
    expect(Object.isFrozen(ctx.grants[0])).toBe(true);
    // A mutation attempt does not change the stored value.
    expect(() => {
      (ctx.grants[0] as { mode: string }).mode = "read_only";
    }).toThrow();
    expect(store.get(ctx.runContext)?.grants[0].mode).toBe("read_write");
  });

  it("is decoupled from later grant-store mutation (snapshot is a copy)", () => {
    const live = [grant()];
    const store = new RunContextStore();
    const ctx = store.mint(snapshot(live));
    // Mutating the caller's array after minting must not change the pinned set.
    live.push(grant({ grantId: "g2" }));
    live[0] = grant({ grantId: "g1", mode: "read_only" });
    expect(ctx.grants).toHaveLength(1);
    expect(ctx.grants[0].mode).toBe("read_write");
  });

  it("releases one context and reports whether it existed", () => {
    const store = new RunContextStore();
    const ctx = store.mint(snapshot());
    expect(store.release(ctx.runContext)).toBe(true);
    expect(store.get(ctx.runContext)).toBeNull();
    expect(store.release(ctx.runContext)).toBe(false); // already gone
  });

  it("clear() drops every context (RAM-only reset)", () => {
    const store = new RunContextStore();
    const a = store.mint(snapshot());
    const b = store.mint(snapshot());
    expect(store.size()).toBe(2);
    store.clear();
    expect(store.size()).toBe(0);
    expect(store.get(a.runContext)).toBeNull();
    expect(store.get(b.runContext)).toBeNull();
  });
});
