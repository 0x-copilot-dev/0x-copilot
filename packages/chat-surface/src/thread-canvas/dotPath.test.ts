// Mirror of the surface-renderers resolver cases (PRD-B2 D2) — the two must
// stay honest twins (the import cycle is why they are duplicated, not shared).

import { describe, expect, it } from "vitest";

import { isSafeHttpUrl, resolveDotPath } from "./dotPath";

describe("resolveDotPath", () => {
  it("reads nested mapping keys", () => {
    expect(resolveDotPath({ a: { b: { c: 7 } } }, "a.b.c")).toBe(7);
  });

  it("indexes arrays with numeric segments", () => {
    expect(
      resolveDotPath({ items: [{ id: "x" }, { id: "y" }] }, "items.1.id"),
    ).toBe("y");
  });

  it("returns undefined on a miss / primitive mid-traversal / null hole", () => {
    expect(resolveDotPath({ a: 1 }, "a.b")).toBeUndefined();
    expect(resolveDotPath({ a: null }, "a.b")).toBeUndefined();
    expect(resolveDotPath({ items: [] }, "items.5")).toBeUndefined();
    expect(resolveDotPath({ items: [1] }, "items.notindex")).toBeUndefined();
  });

  it("returns undefined for an empty / non-string path", () => {
    expect(resolveDotPath({ a: 1 }, "")).toBeUndefined();
    expect(resolveDotPath({ a: 1 }, 5 as unknown as string)).toBeUndefined();
  });
});

describe("isSafeHttpUrl", () => {
  it("accepts http(s) strings only", () => {
    expect(isSafeHttpUrl("https://linear.app/x")).toBe(true);
    expect(isSafeHttpUrl("http://example.com")).toBe(true);
  });

  it("rejects unsafe schemes, relative paths, and non-strings", () => {
    expect(isSafeHttpUrl("javascript:alert(1)")).toBe(false);
    expect(isSafeHttpUrl("data:text/html,x")).toBe(false);
    expect(isSafeHttpUrl("/relative/path")).toBe(false);
    expect(isSafeHttpUrl(42)).toBe(false);
    expect(isSafeHttpUrl(null)).toBe(false);
  });
});
