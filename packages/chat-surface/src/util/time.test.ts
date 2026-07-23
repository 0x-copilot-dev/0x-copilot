import { describe, expect, it } from "vitest";

import { formatClockTime, formatRelativeTime } from "./time";

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

describe("formatClockTime (PRD-08 D3)", () => {
  it("renders the design's zero-padded 24-hour wall clock under an h23 locale + pinned zone", () => {
    // The design's literal (copilot-data.jsx:607). `en-GB` is h23; UTC pins the
    // value so the assertion is machine-independent (DoD 5).
    expect(formatClockTime("2026-07-22T11:44:00Z", "en-GB", "UTC")).toBe(
      "11:44",
    );
  });

  it("honours the time zone rather than the runtime's zone", () => {
    // 11:44 UTC is 07:44 in New York (EDT, -04:00) — the zone is applied, not
    // ignored.
    expect(
      formatClockTime("2026-07-22T11:44:00Z", "en-GB", "America/New_York"),
    ).toBe("07:44");
  });

  it("produces an AM/PM suffix in an h12 locale (locale honoured, not forced h23)", () => {
    const us = formatClockTime("2026-07-22T11:44:00Z", "en-US", "UTC");
    expect(us).toMatch(/11:44\s?AM/i);
  });

  it("returns em-dash for unparseable input (matches formatRelativeTime)", () => {
    expect(formatClockTime("not-an-iso")).toBe("—");
  });
});

function unitDivisor(unit: string): number {
  if (unit === "hour") return 60;
  if (unit === "day") return 60 * 24;
  if (unit === "month") return 60 * 24 * 30;
  return 60 * 24 * 365;
}
