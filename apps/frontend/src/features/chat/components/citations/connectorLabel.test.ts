import { describe, expect, it } from "vitest";
import { humanizeConnector } from "@0x-copilot/chat-surface";

describe("humanizeConnector", () => {
  it.each([
    ["web_search", "Web search"],
    ["notion", "Notion"],
    ["google_drive", "Google drive"],
    ["pagerduty-incidents", "Pagerduty incidents"],
    ["GITHUB", "Github"],
    ["multi__under___scores", "Multi under scores"],
  ])("%s -> %s", (slug, expected) => {
    expect(humanizeConnector(slug)).toBe(expected);
  });

  it("returns the input unchanged when blank", () => {
    expect(humanizeConnector("")).toBe("");
    expect(humanizeConnector("   ")).toBe("   ");
  });

  it("memoises subsequent calls", () => {
    const first = humanizeConnector("notion");
    const second = humanizeConnector("notion");
    expect(first).toBe(second);
  });
});
