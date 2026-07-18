// PR 8.0.1 — pluralize grammar contract.
// PR-1.7 — moved down with the helper; the same assertions run from
// chat-surface.

import { describe, expect, it } from "vitest";
import { TAB_LABELS, pluralize, tabLabel } from "./pluralize";

describe("pluralize", () => {
  it("returns singular when count is 1", () => {
    expect(pluralize("Source", "Sources", 1)).toBe("Source");
  });

  it("returns plural when count is 0", () => {
    expect(pluralize("Source", "Sources", 0)).toBe("Sources");
  });

  it("returns plural when count is 2+", () => {
    expect(pluralize("Source", "Sources", 5)).toBe("Sources");
  });

  it("tabLabel obeys the same rule for the canonical label set", () => {
    expect(tabLabel(TAB_LABELS.sources, 1)).toBe("Source");
    expect(tabLabel(TAB_LABELS.sources, 7)).toBe("Sources");
    expect(tabLabel(TAB_LABELS.agents, 1)).toBe("Agent");
    expect(tabLabel(TAB_LABELS.approval, 1)).toBe("Approval");
    expect(tabLabel(TAB_LABELS.approval, 3)).toBe("Approvals");
    expect(tabLabel(TAB_LABELS.skill, 1)).toBe("Skill");
  });
});
