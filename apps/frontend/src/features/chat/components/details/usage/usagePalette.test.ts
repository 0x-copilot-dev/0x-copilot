import { describe, expect, it } from "vitest";

import { USAGE_PALETTE_OTHER_KEY, usagePalette } from "./usagePalette";

describe("usagePalette", () => {
  it("assigns deterministic colours from the accent ramp", () => {
    const a = usagePalette({ keys: ["alice", "bob"] });
    const b = usagePalette({ keys: ["alice", "bob"] });
    expect(a).toEqual(b);
    expect(a.alice).toBeTruthy();
    expect(a.bob).toBeTruthy();
    expect(a.alice).not.toBe(a.bob);
  });

  it("includes an 'other' colour when requested", () => {
    const palette = usagePalette({ keys: ["alice"], includeOther: true });
    expect(palette[USAGE_PALETTE_OTHER_KEY]).toBeTruthy();
  });

  it("does not include 'other' when not requested", () => {
    const palette = usagePalette({ keys: ["alice"] });
    expect(palette[USAGE_PALETTE_OTHER_KEY]).toBeUndefined();
  });

  it("wraps around the ramp for very large key sets", () => {
    const keys = Array.from({ length: 20 }, (_, i) => `user_${i}`);
    const palette = usagePalette({ keys });
    expect(Object.keys(palette)).toHaveLength(20);
    // Wrap-around: first and ninth (8 swatches) collide.
    expect(palette.user_0).toBe(palette.user_8);
  });
});
