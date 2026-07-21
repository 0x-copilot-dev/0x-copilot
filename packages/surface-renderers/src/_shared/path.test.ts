import { describe, expect, it } from "vitest";

import {
  formatValue,
  isSafeHttpUrl,
  MAX_DISPLAY_CHARS,
  resolvePath,
} from "./path";

describe("resolvePath", () => {
  it("reads nested object keys", () => {
    expect(resolvePath({ a: { b: { c: 7 } } }, "a.b.c")).toBe(7);
  });

  it("indexes arrays with all-digit segments", () => {
    expect(
      resolvePath({ items: [{ id: "x" }, { id: "y" }] }, "items.1.id"),
    ).toBe("y");
  });

  it("returns undefined on any miss without throwing", () => {
    expect(resolvePath({ a: 1 }, "a.b.c")).toBeUndefined();
    expect(resolvePath(null, "a")).toBeUndefined();
    expect(resolvePath(undefined, "a")).toBeUndefined();
    expect(resolvePath({ a: [1, 2] }, "a.name")).toBeUndefined();
    expect(resolvePath({ a: 1 }, "")).toBeUndefined();
  });

  it("does not use a non-digit segment to index an array", () => {
    expect(resolvePath({ list: ["a", "b"] }, "list.length")).toBeUndefined();
  });

  it("traverses 20 levels of nesting iteratively (no stack blowup)", () => {
    let node: Record<string, unknown> = { value: "deep" };
    const segments: string[] = ["value"];
    for (let i = 0; i < 20; i += 1) {
      node = { child: node };
      segments.unshift("child");
    }
    expect(resolvePath(node, segments.join("."))).toBe("deep");
  });
});

describe("formatValue", () => {
  it("returns empty string for null/undefined", () => {
    expect(formatValue(null)).toBe("");
    expect(formatValue(undefined)).toBe("");
  });

  it("formats numbers with tabular grouping and currency", () => {
    expect(formatValue(1234567, "number")).toContain("1");
    expect(formatValue(1234567, "number")).not.toBe("1234567");
    expect(formatValue(84, "currency")).toMatch(/84/);
  });

  it("falls back to raw string for unparseable numbers/dates", () => {
    expect(formatValue("not-a-number", "number")).toBe("not-a-number");
    expect(formatValue("not-a-date", "datetime")).toBe("not-a-date");
  });

  it("renders objects as JSON, never [object Object]", () => {
    expect(formatValue({ a: 1 })).toBe('{"a":1}');
  });

  it("truncates strings longer than the display cap", () => {
    const long = "x".repeat(10_000);
    const out = formatValue(long, "text");
    expect(out.length).toBe(MAX_DISPLAY_CHARS + 1); // + ellipsis
    expect(out.endsWith("…")).toBe(true);
  });
});

describe("isSafeHttpUrl", () => {
  it("accepts http and https", () => {
    expect(isSafeHttpUrl("https://example.com")).toBe(true);
    expect(isSafeHttpUrl("http://example.com")).toBe(true);
  });

  it("rejects javascript:, data:, and non-strings", () => {
    expect(isSafeHttpUrl("javascript:alert(1)")).toBe(false);
    expect(isSafeHttpUrl("data:text/html,<script>")).toBe(false);
    expect(isSafeHttpUrl("mailto:x@y.com")).toBe(false);
    expect(isSafeHttpUrl(42)).toBe(false);
    expect(isSafeHttpUrl(null)).toBe(false);
  });
});
