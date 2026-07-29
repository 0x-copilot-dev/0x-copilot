import { describe, expect, it } from "vitest";

import {
  formatValue,
  isSafeHttpUrl,
  magnitudeShares,
  MAX_DISPLAY_CHARS,
  numericValue,
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

describe("numericValue", () => {
  it("reads finite numbers and numeric strings", () => {
    expect(numericValue(3100)).toBe(3100);
    expect(numericValue("3100")).toBe(3100);
    expect(numericValue("-12.5")).toBe(-12.5);
    expect(numericValue(0)).toBe(0);
  });

  // JS would coerce every one of these to a number. Letting "" become 0 would
  // paint a real magnitude bar for a missing cell.
  it("refuses values that only coerce by accident", () => {
    expect(numericValue("")).toBeNull();
    expect(numericValue("   ")).toBeNull();
    expect(numericValue(true)).toBeNull();
    expect(numericValue(null)).toBeNull();
    expect(numericValue(undefined)).toBeNull();
    expect(numericValue([])).toBeNull();
    expect(numericValue("21,850 USDC")).toBeNull();
    expect(numericValue(Number.NaN)).toBeNull();
    expect(numericValue(Number.POSITIVE_INFINITY)).toBeNull();
  });
});

describe("magnitudeShares", () => {
  it("scales each value against the column's largest magnitude", () => {
    expect(magnitudeShares([100, 50, 25])).toEqual([1, 0.5, 0.25]);
  });

  it("keeps holes as holes instead of scoring them zero", () => {
    expect(magnitudeShares([100, null, 50])).toEqual([1, null, 0.5]);
    expect(magnitudeShares([100, "n/a", 50])).toEqual([1, null, 0.5]);
  });

  // A bar encodes SIZE. Two rows at +1000 and -1000 are the same size, and the
  // sign stays readable in the number, which the bar never covers.
  it("compares magnitude, so sign does not shorten a bar", () => {
    expect(magnitudeShares([-1000, 1000, 500])).toEqual([1, 1, 0.5]);
  });

  // The three cases where a bar would state a comparison that does not exist.
  it("paints nothing when there is no comparison to make", () => {
    expect(magnitudeShares([100])).toEqual([null]);
    expect(magnitudeShares([100, null])).toEqual([null, null]);
    expect(magnitudeShares([7, 7, 7])).toEqual([null, null, null]);
    expect(magnitudeShares([0, 0])).toEqual([null, null]);
    expect(magnitudeShares([])).toEqual([]);
  });

  it("is total over a hostile payload", () => {
    expect(magnitudeShares([{}, [], "x", true])).toEqual([
      null,
      null,
      null,
      null,
    ]);
  });
});

describe("magnitudeShares — unreadable numbers (regression)", () => {
  // Locale-formatted values are ordinary tool output. Scaling the column to
  // only the values Number() happens to parse ranks rows by parseability: here
  // 987 is the SMALLEST value but would be painted as the column maximum.
  it("paints nothing when a value carries a magnitude it could not read", () => {
    expect(magnitudeShares(["1,234", "987", "2,500", "555"])).toEqual([
      null,
      null,
      null,
      null,
    ]);
    expect(magnitudeShares([1500, 2300, 900, "1,750"])).toEqual([
      null,
      null,
      null,
      null,
    ]);
  });

  // A digit-free placeholder is a genuinely empty cell, not a failed read, so
  // the rest of the column still scales.
  it("still scales a column whose gaps carry no digits", () => {
    expect(magnitudeShares([100, "n/a", 50])).toEqual([1, null, 0.5]);
    expect(magnitudeShares([100, "—", 50, null])).toEqual([1, null, 0.5, null]);
  });
});
