import { describe, expect, it } from "vitest";

import {
  DEFAULT_RAIL_WIDTH,
  MAX_RAIL_WIDTH,
  MIN_RAIL_WIDTH,
  clampRailWidth,
} from "../../thread-canvas";
import { RAIL_WIDTH_KEY, readRailWidth } from "./useRailWidth";

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

  it("returns the default when nothing is persisted", () => {
    expect(readRailWidth(storeOf(null))).toBe(DEFAULT_RAIL_WIDTH);
  });

  it("parses and clamps a persisted value", () => {
    expect(readRailWidth(storeOf("440"))).toBe(440);
    expect(readRailWidth(storeOf("100000"))).toBe(MAX_RAIL_WIDTH);
  });

  it("falls back to the default for an unparseable value", () => {
    expect(readRailWidth(storeOf("not-a-number"))).toBe(DEFAULT_RAIL_WIDTH);
  });
});
