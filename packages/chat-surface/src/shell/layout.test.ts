import { describe, expect, it } from "vitest";

import {
  DEFAULT_SHELL_WIDTH_CLASS,
  SHELL_BREAKPOINTS,
  widthClassFor,
} from "./layout";

// PRD-00 FR-0.1 — this file is the only place the numbers live. The boundary
// cases matter more than the middles: an off-by-one here silently gives a
// 720px window the compact layout (or denies a 719px one).
describe("widthClassFor", () => {
  it("classifies the three bands", () => {
    expect(widthClassFor(320)).toBe("compact");
    expect(widthClassFor(640)).toBe("compact");
    expect(widthClassFor(900)).toBe("regular");
    expect(widthClassFor(1180)).toBe("wide");
    expect(widthClassFor(2560)).toBe("wide");
  });

  it("treats each breakpoint as the FIRST width of the wider class", () => {
    expect(widthClassFor(SHELL_BREAKPOINTS.compact - 1)).toBe("compact");
    expect(widthClassFor(SHELL_BREAKPOINTS.compact)).toBe("regular");
    expect(widthClassFor(SHELL_BREAKPOINTS.regular - 1)).toBe("regular");
    expect(widthClassFor(SHELL_BREAKPOINTS.regular)).toBe("wide");
  });

  it("falls back to the default for unmeasured widths", () => {
    // A detached node / display:none ancestor / first frame reports 0. That is
    // "we don't know yet", not "the window is tiny" — claiming compact there
    // would flash the narrow layout on every mount.
    expect(widthClassFor(0)).toBe(DEFAULT_SHELL_WIDTH_CLASS);
    expect(widthClassFor(-10)).toBe(DEFAULT_SHELL_WIDTH_CLASS);
    expect(widthClassFor(Number.NaN)).toBe(DEFAULT_SHELL_WIDTH_CLASS);
    expect(widthClassFor(Number.POSITIVE_INFINITY)).toBe(
      DEFAULT_SHELL_WIDTH_CLASS,
    );
  });

  it("defaults to the widest class, so the first paint is the historical layout", () => {
    expect(DEFAULT_SHELL_WIDTH_CLASS).toBe("wide");
  });
});
