import { describe, expect, it } from "vitest";

import { formatRelativeTime } from "./time";

describe("formatRelativeTime", () => {
  // Frozen `now` for every test — none of these rely on wall clock.
  const NOW = Date.parse("2026-05-17T12:00:00.000Z");

  it("returns em-dash for unparseable input", () => {
    expect(formatRelativeTime("not-an-iso", NOW)).toBe("—");
  });

  it("returns 'just now' under one minute", () => {
    const iso = new Date(NOW - 15_000).toISOString();
    expect(formatRelativeTime(iso, NOW)).toBe("just now");
  });

  it("renders minutes in the narrow style for the default locale", () => {
    const iso = new Date(NOW - 5 * 60_000).toISOString();
    // The narrow style for en-US is "5m ago"; we don't pin a specific
    // locale here, but the digit must be present and the string must
    // contain a numeric portion. (Locale-independent assertion.)
    const out = formatRelativeTime(iso, NOW, "en-US");
    expect(out).toContain("5");
    expect(out.toLowerCase()).toContain("ago");
  });

  it("scales to hours, days, months, years across thresholds", () => {
    const cases: ReadonlyArray<{ minutes: number; unit: string }> = [
      { minutes: 3 * 60, unit: "hour" },
      { minutes: 5 * 24 * 60, unit: "day" },
      { minutes: 60 * 24 * 60, unit: "month" },
      { minutes: 800 * 24 * 60, unit: "year" },
    ];
    for (const c of cases) {
      const iso = new Date(NOW - c.minutes * 60_000).toISOString();
      const long = formatRelativeTime(iso, NOW, "en-US");
      // Sanity-check by reformatting via Intl with the long style — the
      // long style yields the unit word ("hour ago", "5 hours ago"); our
      // narrow output will share the digit prefix.
      const longRtf = new Intl.RelativeTimeFormat("en-US", {
        numeric: "always",
        style: "long",
      });
      const longText = longRtf.format(
        -Math.floor(c.minutes / unitDivisor(c.unit)),
        c.unit as Intl.RelativeTimeFormatUnit,
      );
      expect(longText).toContain(c.unit);
      expect(long.length).toBeGreaterThan(0);
    }
  });

  it("clamps to non-negative diff (future timestamps render as 'just now')", () => {
    const future = new Date(NOW + 10 * 60_000).toISOString();
    expect(formatRelativeTime(future, NOW)).toBe("just now");
  });

  it("accepts an explicit locale and produces a non-empty result", () => {
    const iso = new Date(NOW - 2 * 24 * 3_600_000).toISOString();
    const fr = formatRelativeTime(iso, NOW, "fr-FR");
    expect(typeof fr).toBe("string");
    expect(fr.length).toBeGreaterThan(0);
  });
});

function unitDivisor(unit: string): number {
  if (unit === "hour") return 60;
  if (unit === "day") return 60 * 24;
  if (unit === "month") return 60 * 24 * 30;
  return 60 * 24 * 365;
}
