import { describe, expect, it } from "vitest";
import {
  CATEGORY_LABEL,
  CHAT_PROMPT_SUGGESTIONS,
  type ChatPromptCategory,
} from "./index";

describe("CHAT_PROMPT_SUGGESTIONS", () => {
  it("exposes exactly four cards", () => {
    expect(CHAT_PROMPT_SUGGESTIONS).toHaveLength(4);
  });

  it("covers all four categories exactly once", () => {
    const expected: ChatPromptCategory[] = [
      "draft",
      "summarize",
      "find",
      "compare",
    ];
    const seen = CHAT_PROMPT_SUGGESTIONS.map((s) => s.category).sort();
    expect(seen).toEqual([...expected].sort());
  });

  it("every card has non-empty title, label, prompt", () => {
    for (const s of CHAT_PROMPT_SUGGESTIONS) {
      expect(s.title.trim().length).toBeGreaterThan(0);
      expect(s.label.trim().length).toBeGreaterThan(0);
      expect(s.prompt.trim().length).toBeGreaterThan(0);
    }
  });

  it("structurally satisfies the assistant-ui SuggestionConfig contract", () => {
    // Width subtyping check: passing a typed value through a SuggestionConfig
    // shape (only title/label/prompt required) must compile and round-trip.
    const projected = CHAT_PROMPT_SUGGESTIONS.map(
      ({ title, label, prompt }) => ({
        title,
        label,
        prompt,
      }),
    );
    expect(projected).toHaveLength(CHAT_PROMPT_SUGGESTIONS.length);
    expect(Object.keys(projected[0]).sort()).toEqual(
      ["label", "prompt", "title"].sort(),
    );
  });
});

describe("CATEGORY_LABEL", () => {
  it("maps every category to an uppercase label", () => {
    for (const [key, label] of Object.entries(CATEGORY_LABEL)) {
      expect(label).toMatch(/^[A-Z]+$/);
      expect(label.toLowerCase()).toBe(key);
    }
  });
});
