import { describe, expect, it } from "vitest";

import { createShikiHighlighter } from "./shikiHighlighter";
import { MAX_HIGHLIGHTED_CHARS } from "./syntaxTokens";

// These drive the REAL shiki, not a fake. A fake would prove the seam and
// nothing about whether the grammars we named actually load, which is the half
// that can silently fail: `@shikijs/langs/<id>` is a subpath export, and a
// wrong id resolves to a module-not-found at runtime, in a lazy import, inside
// a `catch` that degrades to plain text. Every test here would still pass
// against a stub.
describe("shiki highlighter", () => {
  it("answers null before a grammar is resident, and never throws doing it", () => {
    const highlighter = createShikiHighlighter();
    expect(highlighter.isReady("tsx")).toBe(false);
    expect(highlighter.tokenize("const x = 1;", "tsx")).toBeNull();
  });

  it("colours tokens with CSS variables, never a theme's hex", async () => {
    const highlighter = createShikiHighlighter();
    await highlighter.load("tsx");

    const lines = highlighter.tokenize("// note\nconst answer = 42;", "tsx");
    expect(lines).not.toBeNull();
    expect(lines).toHaveLength(2);

    const colours = lines!.flatMap((line) =>
      line
        .map((token) => token.color)
        .filter((color): color is string => !!color),
    );
    expect(colours.length).toBeGreaterThan(0);
    // The whole reason one tokenizer pass serves dark, light and slate.
    for (const colour of colours) {
      expect(colour).toMatch(/^var\(--syntax-/);
    }

    const keyword = lines![1]!.find((token) => token.content === "const");
    expect(keyword?.color).toBe("var(--syntax-token-keyword)");
    const comment = lines![0]!.map((token) => token.color);
    expect(comment).toContain("var(--syntax-token-comment)");
  });

  it("loads a second grammar without disturbing the first", async () => {
    const highlighter = createShikiHighlighter();
    await highlighter.load("python");
    await highlighter.load("yaml");
    expect(highlighter.isReady("python")).toBe(true);
    expect(highlighter.isReady("yaml")).toBe(true);
    expect(highlighter.tokenize("def f():\n    pass", "python")).not.toBeNull();
    expect(highlighter.tokenize("a: 1", "yaml")).not.toBeNull();
  });

  it("is idempotent under concurrent loads of the same grammar", async () => {
    const highlighter = createShikiHighlighter();
    await Promise.all([
      highlighter.load("json"),
      highlighter.load("json"),
      highlighter.load("json"),
    ]);
    expect(highlighter.tokenize('{"a":1}', "json")).not.toBeNull();
  });

  it("declines a language it carries no grammar for, without loading anything", async () => {
    const highlighter = createShikiHighlighter();
    expect(highlighter.supports("cobol")).toBe(false);
    await highlighter.load("cobol");
    expect(highlighter.isReady("cobol")).toBe(false);
    expect(
      highlighter.tokenize("IDENTIFICATION DIVISION.", "cobol"),
    ).toBeNull();
  });

  it("refuses a block past the size budget rather than tokenizing it per frame", async () => {
    const highlighter = createShikiHighlighter();
    await highlighter.load("tsx");
    const huge = "const x = 1;\n".repeat(
      Math.ceil(MAX_HIGHLIGHTED_CHARS / 12) + 1,
    );
    expect(huge.length).toBeGreaterThan(MAX_HIGHLIGHTED_CHARS);
    expect(highlighter.tokenize(huge, "tsx")).toBeNull();
    // …and still colours a block just under it.
    expect(highlighter.tokenize("const x = 1;", "tsx")).not.toBeNull();
  });
});
