import { describe, expect, it } from "vitest";

import { conversationTitleFromPrompt } from "./conversationTitle";

/**
 * These cases are mirrored by
 * `services/ai-backend/tests/unit/agent_runtime/api/test_conversation_title.py`.
 * The server fills an empty title from the same prompt, so a divergence here is
 * a conversation whose name changes depending on which side named it.
 */
describe("conversationTitleFromPrompt", () => {
  it("keeps a short prompt verbatim", () => {
    expect(conversationTitleFromPrompt("are there any csv in the folder")).toBe(
      "are there any csv in the folder",
    );
  });

  it("collapses newlines and runs of whitespace", () => {
    // A pasted multi-line prompt otherwise stores its line breaks into a
    // single-line header.
    expect(conversationTitleFromPrompt("fix   the\n\nbuild  please")).toBe(
      "fix the build please",
    );
  });

  it("falls back when the prompt is empty (attachment-only send)", () => {
    expect(conversationTitleFromPrompt("   ")).toBe("New chat");
    expect(conversationTitleFromPrompt("", "First run")).toBe("First run");
  });

  it("cuts on a word boundary and marks the cut", () => {
    // The reported string: 60 chars, mid-word, unmarked — "…the official Py".
    const prompt =
      "Use the web_search tool exactly once to find the official Python " +
      "documentation page for math.isqrt.";
    const title = conversationTitleFromPrompt(prompt);
    expect(title).toBe(
      "Use the web_search tool exactly once to find the official…",
    );
    expect(title.endsWith("Py")).toBe(false);
    expect(title.endsWith("…")).toBe(true);
  });

  it("never exceeds the cap, ellipsis included", () => {
    const title = conversationTitleFromPrompt("x".repeat(500));
    expect(title.length).toBeLessThanOrEqual(61);
  });

  it("cuts hard when there is no usable word boundary", () => {
    // One unbroken token — a path, a URL, a hash. A word-boundary cut here
    // would leave almost nothing, so the cap wins and the ellipsis still marks
    // it as truncated.
    const title = conversationTitleFromPrompt("a".repeat(120));
    expect(title).toBe(`${"a".repeat(60)}…`);
  });

  it("does not leave dangling punctuation before the ellipsis", () => {
    const prompt =
      "Please review the deployment checklist, the runbook, and the rollback " +
      "plan before Friday.";
    expect(conversationTitleFromPrompt(prompt)).not.toContain(",…");
  });
});
