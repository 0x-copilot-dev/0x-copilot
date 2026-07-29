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

  // `Number()` reads every one of these as a quantity. They are names: an order
  // number, a hex colour, a phone number. Sizing or tabulating them states a
  // comparison that the values do not support.
  it("refuses identifier-shaped strings that merely coerce to a number", () => {
    expect(numericValue("007")).toBeNull(); // an order number, not seven
    expect(numericValue("0123")).toBeNull();
    expect(numericValue("0x1F")).toBeNull(); // Number() reads this as 31
    expect(numericValue("0b101")).toBeNull(); // …and this as 5
    expect(numericValue("0o17")).toBeNull(); // …and this as 15
    expect(numericValue("+14155550123")).toBeNull(); // a phone number
    expect(numericValue("+5")).toBeNull();
  });

  // The other half of the same rule: shapes that look unusual but really are
  // magnitudes must keep the register.
  it("keeps the spellings that really are magnitudes", () => {
    expect(numericValue("1e5")).toBe(100_000); // scientific notation
    expect(numericValue("-1.5E-3")).toBe(-0.0015);
    expect(numericValue("-0")).toBe(-0); // genuinely zero, not an id
    expect(numericValue("0")).toBe(0);
    expect(numericValue("0.5")).toBe(0.5);
    expect(numericValue("3.14")).toBe(3.14);
    expect(numericValue("1000")).toBe(1000);
    expect(numericValue("-12.5")).toBe(-12.5);
  });

  // An omitted redundant zero is not an added mark: ".5" has one reading and
  // only one, and no identifier scheme elides — schemes pad. Refusing them was
  // never worth one missing bar either, because `isUnreadableNumber` promotes
  // any digit-carrying string the grammar rejects into whole-column suppression.
  it("reads the lenient decimal spellings as the magnitudes they are", () => {
    expect(numericValue(".5")).toBe(0.5);
    expect(numericValue("-.5")).toBe(-0.5);
    expect(numericValue("12.")).toBe(12);
    expect(numericValue("-12.")).toBe(-12);
    expect(numericValue("0.")).toBe(0);
    expect(numericValue(".5e3")).toBe(500);
    expect(numericValue("12.e-1")).toBe(1.2);
  });

  // Widening the fraction must not widen anything else: a point is not a
  // licence, and the padding rule still holds on both sides of it.
  it("still refuses shapes that are no number's spelling", () => {
    expect(numericValue(".")).toBeNull();
    expect(numericValue("-")).toBeNull();
    expect(numericValue("-.")).toBeNull();
    expect(numericValue("12..")).toBeNull();
    expect(numericValue(".5.")).toBeNull();
    expect(numericValue("1.2.3")).toBeNull();
    expect(numericValue("1e")).toBeNull();
    expect(numericValue("e5")).toBeNull();
    expect(numericValue("00.5")).toBeNull(); // padded, so still an id shape
    expect(numericValue("007.")).toBeNull();
    expect(numericValue(".5,5")).toBeNull();
  });

  // CSV cells arrive with the separator's padding attached; that is
  // presentation whitespace, not a different value.
  it("reads a padded cell as the number it is", () => {
    expect(numericValue(" 42 ")).toBe(42);
    expect(numericValue("\t-7\n")).toBe(-7);
  });

  // Well-spelled and still not a magnitude: a bar of infinite length is no bar.
  it("refuses an in-grammar literal that overflows to Infinity", () => {
    expect(numericValue("1e400")).toBeNull();
    expect(numericValue("-1e400")).toBeNull();
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

describe("magnitudeShares — identifiers are not magnitudes (regression)", () => {
  // Order numbers and phone numbers coerce cleanly under `Number()`, so this
  // column used to be scaled: "007" drew a bar a sixteenth the length of
  // "113"'s, as though the ids ranked by size. They rank by nothing.
  it("paints nothing for a column of identifiers", () => {
    expect(magnitudeShares(["007", "042", "113"])).toEqual([null, null, null]);
    expect(magnitudeShares(["+14155550123", "+14155550124"])).toEqual([
      null,
      null,
    ]);
  });

  // Same fail-closed rule the separator case gets, and for the same reason:
  // nothing tells us whether "0999999" is an id or a padded quantity, and if it
  // is a quantity it is the column maximum — scoring it as a hole would hand a
  // full bar to 200 and none to the largest value in the column.
  it("suppresses a numeric column carrying one identifier-shaped value", () => {
    expect(magnitudeShares([100, 200, "0999999"])).toEqual([null, null, null]);
  });

  // The stricter reading must not cost the column bars it had honestly earned.
  it("still scales values spelled the way numbers are spelled", () => {
    expect(magnitudeShares(["1e2", "50", "-0"])).toEqual([1, 0.5, 0]);
    expect(magnitudeShares([" 100 ", "25"])).toEqual([1, 0.25]);
  });
});

describe("magnitudeShares — the grammar's edges (regression)", () => {
  // One lenient spelling used to erase every bar beside it: this column was
  // [0.75, 0.25, 1] before ".5" was excluded, and [null, null, null] after,
  // because a digit-carrying string the grammar rejects suppresses the column.
  // Whatever the grammar refuses, it refuses at the width of the column.
  it("does not erase a column because one cell dropped a redundant zero", () => {
    expect(magnitudeShares(["1.5", ".5", "2"])).toEqual([0.75, 0.25, 1]);
    expect(magnitudeShares(["12.", "6"])).toEqual([1, 0.5]);
    expect(magnitudeShares([100, "-.5", 50])).toEqual([1, 0.005, 0.5]);
  });

  // The price of refusing the phone-number spelling, pinned so it stays a
  // decision. "+12" is a delta and "-4" is flawlessly in grammar; both lose
  // their bar because nothing local to "+12" tells it from "+14155550123".
  // Erring the other way would scale a column of phone numbers by dialling
  // code and print "+12" as "12" — see `numericValue`.
  it("suppresses a signed-delta column, the cost of the leading-+ refusal", () => {
    expect(magnitudeShares(["+12", "-4", "+30"])).toEqual([null, null, null]);
  });
});

describe("formatValue and the bars agree about what a magnitude is", () => {
  // The defect: `Number("007")` is 7, so a `format: "number"` cell PRINTED "7"
  // while `magnitudeShares` — reading "007" as an identifier — gave it no bar.
  // One cell, two answers, and the printed one destroyed the only thing that
  // made the value recognisable as an order number.
  it.each(["007", "0x1F", "+14155550123", "0b101"])(
    "prints the identifier %s as it arrived, and gives it no bar",
    (identifier) => {
      expect(formatValue(identifier, "number")).toBe(identifier);
      expect(numericValue(identifier)).toBeNull();
    },
  );

  // The other edge of the same rule. A dropped zero carried no information, so
  // printing it back canonically destroys nothing — unlike "007" → "7", where
  // the padding WAS the identifier.
  it("prints a lenient decimal as the magnitude it is", () => {
    expect(formatValue(".5", "number")).toBe(
      new Intl.NumberFormat(undefined).format(0.5),
    );
    expect(formatValue("12.", "number")).toBe(
      new Intl.NumberFormat(undefined).format(12),
    );
  });

  // The leading `+` is a bet, not a reading, so the losing side is recorded
  // rather than hidden: this IS a delta, and it is exactly what Intl emits
  // under signDisplay. The half the bet wins is asserted on the same value —
  // its sign survives the print, where accepting the spelling would reformat
  // "+12" to "12" and delete the mark that made it a delta.
  it("leaves a signed delta unsized, and keeps its sign intact", () => {
    const emitted = new Intl.NumberFormat("en-US", {
      signDisplay: "always",
    }).format(12);
    expect(emitted).toBe("+12");
    expect(numericValue(emitted)).toBeNull();
    expect(formatValue(emitted, "number")).toBe("+12");
  });

  it("still formats real magnitudes, including awkward spellings", () => {
    expect(formatValue(1000, "number")).toContain("1");
    expect(formatValue("1e5", "number")).toBe(
      new Intl.NumberFormat(undefined).format(100_000),
    );
    expect(formatValue("-12.5", "number")).toBe(
      new Intl.NumberFormat(undefined).format(-12.5),
    );
    expect(numericValue("1e5")).toBe(100_000);
  });

  // The property, stated once: anything the bars refuse to size, the formatter
  // refuses to reformat.
  it.each(["007", "0x1F", "1,234", "$1.2k", "21,850 USDC", "not-a-number", ""])(
    "never reformats %s, which the bars cannot size either",
    (value) => {
      expect(numericValue(value)).toBeNull();
      expect(formatValue(value, "currency")).toBe(value);
    },
  );
});

describe("resolvePath reads own properties only", () => {
  // Specs are untrusted: `specFromState` checks two fields, and the event
  // projector merges payload state verbatim. A path naming an inherited member
  // printed JS internals as if the tool had returned them.
  it.each(["constructor", "toString", "valueOf", "hasOwnProperty"])(
    "returns undefined for the inherited member %s",
    (inherited) => {
      expect(resolvePath({ title: "real" }, inherited)).toBeUndefined();
    },
  );

  it("does not read __proto__ as a value", () => {
    expect(resolvePath({ title: "real" }, "__proto__")).toBeUndefined();
    expect(resolvePath({ a: { b: 1 } }, "a.__proto__.b")).toBeUndefined();
  });

  it("still reads an own property that shadows an inherited name", () => {
    expect(resolvePath({ constructor: "mine" }, "constructor")).toBe("mine");
    expect(resolvePath({ toString: "also mine" }, "toString")).toBe(
      "also mine",
    );
  });

  it("still reads a null-prototype object", () => {
    const bare = Object.create(null) as Record<string, unknown>;
    bare.title = "from a bare object";
    expect(resolvePath(bare, "title")).toBe("from a bare object");
  });
});
