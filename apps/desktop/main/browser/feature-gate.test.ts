// @vitest-environment node
import { describe, expect, it } from "vitest";

import { DESKTOP_BROWSER_FLAG, isDesktopBrowserEnabled } from "./feature-gate";

describe("isDesktopBrowserEnabled", () => {
  it("is off by default (unset / empty / falsy)", () => {
    expect(isDesktopBrowserEnabled({})).toBe(false);
    expect(isDesktopBrowserEnabled({ [DESKTOP_BROWSER_FLAG]: "" })).toBe(false);
    expect(isDesktopBrowserEnabled({ [DESKTOP_BROWSER_FLAG]: "0" })).toBe(
      false,
    );
    expect(isDesktopBrowserEnabled({ [DESKTOP_BROWSER_FLAG]: "false" })).toBe(
      false,
    );
    expect(isDesktopBrowserEnabled({ [DESKTOP_BROWSER_FLAG]: "off" })).toBe(
      false,
    );
    expect(
      isDesktopBrowserEnabled({ [DESKTOP_BROWSER_FLAG]: "nonsense" }),
    ).toBe(false);
  });

  it("is on only for explicit truthy values", () => {
    for (const v of ["1", "true", "yes", "on", "enabled", "  TRUE  "]) {
      expect(isDesktopBrowserEnabled({ [DESKTOP_BROWSER_FLAG]: v })).toBe(true);
    }
  });
});
