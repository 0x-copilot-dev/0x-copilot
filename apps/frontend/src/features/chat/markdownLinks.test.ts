import { markdownLinkLabel } from "@enterprise-search/chat-surface";
import { describe, expect, it } from "vitest";

describe("markdownLinkLabel", () => {
  it("keeps descriptive markdown link labels", () => {
    expect(
      markdownLinkLabel(
        "https://app.clickup.com/t/86d2twk9u",
        "Work Smarter with ClickUp AI",
      ),
    ).toBe("Work Smarter with ClickUp AI");
  });

  it("compacts raw URL labels", () => {
    expect(
      markdownLinkLabel(
        "https://app.clickup.com/t/86d2twk9u",
        "https://app.clickup.com/t/86d2twk9u",
      ),
    ).toBe("app.clickup.com/t/86d2twk9u");
  });

  it("compacts raw URL labels from split text nodes", () => {
    expect(
      markdownLinkLabel("https://example.com/docs/search", [
        "https://example.com",
        "/docs/search",
      ]),
    ).toBe("example.com/docs/search");
  });
});
