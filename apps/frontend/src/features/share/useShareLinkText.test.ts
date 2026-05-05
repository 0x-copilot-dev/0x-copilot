import { describe, expect, it } from "vitest";

import { useShareLinkText } from "./useShareLinkText";

describe("useShareLinkText", () => {
  it("uses the chat title when present", () => {
    const result = useShareLinkText({
      chatTitle: "Q1 launch announcement",
      chatUrl: "https://app.example.com/c/abc",
    });
    expect(result.title).toBe("Q1 launch announcement");
    expect(result.body).toContain("Q1 launch announcement");
    expect(result.body).toContain("https://app.example.com/c/abc");
  });

  it("falls back to a generic title when none is supplied", () => {
    const result = useShareLinkText({
      chatTitle: null,
      chatUrl: "https://app.example.com/c/abc",
    });
    expect(result.title).toBe("Atlas conversation");
  });

  it("trims whitespace-only titles to the fallback", () => {
    const result = useShareLinkText({
      chatTitle: "   ",
      chatUrl: "https://x",
    });
    expect(result.title).toBe("Atlas conversation");
  });
});
