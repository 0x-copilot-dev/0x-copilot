import { describe, expect, it } from "vitest";

import { formatMicroUsd } from "./formatMicroUsd";

describe("formatMicroUsd", () => {
  it("renders an em dash for null", () => {
    expect(formatMicroUsd(null)).toBe("—");
  });

  it("renders an em dash for undefined", () => {
    expect(formatMicroUsd(undefined)).toBe("—");
  });

  it("rounds 1.234567 USD to two fraction digits", () => {
    expect(formatMicroUsd(1_234_567)).toContain("1.23");
  });

  it("renders zero as $0.00", () => {
    expect(formatMicroUsd(0)).toContain("0.00");
  });

  it("renders four fraction digits when precise", () => {
    expect(formatMicroUsd(1_234_567, { precise: true })).toContain("1.2346");
  });

  it("never returns NaN for nonzero values", () => {
    expect(formatMicroUsd(500_000)).not.toContain("NaN");
  });
});
