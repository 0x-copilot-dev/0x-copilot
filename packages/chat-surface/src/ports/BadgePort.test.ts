import { describe, expect, it, vi } from "vitest";

import type { ShellDestinationSlug } from "../shell/destinations";

import type { BadgePort } from "./BadgePort";

describe("BadgePort contract", () => {
  it("accepts a setBadge(slug, count) call shape", () => {
    const setBadge = vi.fn();
    const port: BadgePort = { setBadge };
    const slug: ShellDestinationSlug = "inbox";
    port.setBadge(slug, 5);
    expect(setBadge).toHaveBeenCalledWith("inbox", 5);
  });

  it("treats count=0 as a clear (per cross-audit §1.2)", () => {
    const setBadge = vi.fn();
    const port: BadgePort = { setBadge };
    port.setBadge("inbox", 0);
    expect(setBadge).toHaveBeenLastCalledWith("inbox", 0);
  });
});
