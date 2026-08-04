import { describe, expect, it } from "vitest";

import {
  DEFAULT_RAIL_WIDTH,
  MAX_RAIL_WIDTH,
  MIN_RAIL_WIDTH,
  clampRailWidth,
} from "../../thread-canvas";
import {
  COCKPIT_RAIL_WIDTH_FRACTION,
  RAIL_WIDTH_KEY,
  cockpitDefaultRailWidth,
  readRailWidth,
} from "./useRailWidth";

describe("clampRailWidth", () => {
  it("keeps an in-range value (rounded)", () => {
    expect(clampRailWidth(420.6)).toBe(421);
  });

  it("clamps below the minimum and above the maximum", () => {
    expect(clampRailWidth(10)).toBe(MIN_RAIL_WIDTH);
    expect(clampRailWidth(9999)).toBe(MAX_RAIL_WIDTH);
  });

  it("falls back to the default for a non-finite value", () => {
    expect(clampRailWidth(Number.NaN)).toBe(DEFAULT_RAIL_WIDTH);
    expect(clampRailWidth(Number.POSITIVE_INFINITY)).toBe(DEFAULT_RAIL_WIDTH);
  });
});

describe("readRailWidth", () => {
  const storeOf = (value: string | null) => ({
    get: (key: string) => (key === RAIL_WIDTH_KEY ? value : null),
  });

  it("reports no preference when nothing is persisted", () => {
    expect(readRailWidth(storeOf(null))).toBeNull();
  });

  it("parses and clamps a persisted value", () => {
    expect(readRailWidth(storeOf("440"))).toBe(440);
    expect(readRailWidth(storeOf("100000"))).toBe(MAX_RAIL_WIDTH);
  });

  it("reports no preference for an unparseable value", () => {
    expect(readRailWidth(storeOf("not-a-number"))).toBeNull();
  });
});

describe("cockpitDefaultRailWidth", () => {
  it("gives chat its share of the canvas and the surface the rest", () => {
    // The point of the fraction: the split is the same at every window size.
    for (const canvas of [1200, 1440, 1680]) {
      const rail = cockpitDefaultRailWidth(canvas);
      expect(rail / canvas).toBeCloseTo(COCKPIT_RAIL_WIDTH_FRACTION, 2);
      expect(rail / canvas).toBeLessThan(0.35);
    }
  });

  it("clamps at both ends rather than following the fraction off a cliff", () => {
    // A narrow cockpit still owes the composer a usable width…
    expect(cockpitDefaultRailWidth(600)).toBe(MIN_RAIL_WIDTH);
    // …and a very wide one must not hand a third of a wall display to chat.
    expect(cockpitDefaultRailWidth(4000)).toBe(MAX_RAIL_WIDTH);
  });
});
