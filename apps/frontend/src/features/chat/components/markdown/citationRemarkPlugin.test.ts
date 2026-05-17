import {
  CITATION_HREF_PREFIX,
  CITATION_ORDINAL_HREF_PREFIX,
  createRemarkCitations,
} from "@enterprise-search/chat-surface";
import remarkParse from "remark-parse";
import remarkStringify from "remark-stringify";
import { unified } from "unified";
import { describe, expect, it } from "vitest";

// Shared plugin instance with no debug sink — the tests assert mdast
// rewriting, not the diagnostic callback.
const remarkCitations = createRemarkCitations();

function transform(input: string): string {
  return unified()
    .use(remarkParse)
    .use(remarkCitations)
    .use(remarkStringify)
    .processSync(input)
    .toString();
}

describe("remarkCitations", () => {
  it("rewrites a closed [c<id>] token into a citation link", () => {
    const out = transform("Per the positioning [c1] and the GTM plan.");
    expect(out).toContain(`[\\[c1\\]](${CITATION_HREF_PREFIX}c1)`);
  });

  it("rewrites multiple tokens in one paragraph", () => {
    const out = transform("[c1] then [c2] then [c10].");
    expect(out).toContain(`(${CITATION_HREF_PREFIX}c1)`);
    expect(out).toContain(`(${CITATION_HREF_PREFIX}c2)`);
    expect(out).toContain(`(${CITATION_HREF_PREFIX}c10)`);
  });

  it("leaves partial tokens alone (streaming-safe)", () => {
    // Mid-stream the closing `]` may not have arrived yet.
    const out = transform("Per the positioning [c");
    expect(out).not.toContain(CITATION_HREF_PREFIX);
    expect(out).toContain("\\[c");
  });

  it("does not match malformed token bodies", () => {
    const out = transform("Reference like [c-1] or [foo] should not chip.");
    expect(out).not.toContain(CITATION_HREF_PREFIX);
  });

  it("does not chip inside an existing link", () => {
    const out = transform("See [my [c1] note](https://example.com).");
    expect(out).not.toContain(CITATION_HREF_PREFIX);
  });

  it("does not chip inside inline code", () => {
    const out = transform("Code looks like `[c1]` literally.");
    expect(out).not.toContain(CITATION_HREF_PREFIX);
  });

  // PR 1.1-rev2 — model-declared `[[N]]` chip format.

  it("rewrites a closed [[N]] token into an ordinal citation link", () => {
    const out = transform("Per the strategy [[3]] and the timing.");
    expect(out).toContain(`(${CITATION_ORDINAL_HREF_PREFIX}3)`);
  });

  it("rewrites multiple ordinal tokens", () => {
    const out = transform("[[1]] then [[2]] then [[10]].");
    expect(out).toContain(`(${CITATION_ORDINAL_HREF_PREFIX}1)`);
    expect(out).toContain(`(${CITATION_ORDINAL_HREF_PREFIX}2)`);
    expect(out).toContain(`(${CITATION_ORDINAL_HREF_PREFIX}10)`);
  });

  it("normalizes leading zeros on ordinals", () => {
    const out = transform("Hi [[007]] there.");
    expect(out).toContain(`(${CITATION_ORDINAL_HREF_PREFIX}7)`);
    expect(out).not.toContain(`(${CITATION_ORDINAL_HREF_PREFIX}007)`);
  });

  it("leaves partial [[N tokens alone (streaming-safe)", () => {
    const out = transform("Per [[3");
    expect(out).not.toContain(CITATION_ORDINAL_HREF_PREFIX);
  });

  it("leaves single bracket non-tokens alone", () => {
    const out = transform("step [3] not a citation.");
    expect(out).not.toContain(CITATION_ORDINAL_HREF_PREFIX);
  });

  it("does not chip ordinal tokens inside inline code", () => {
    const out = transform("Code says `[[3]]` literally.");
    expect(out).not.toContain(CITATION_ORDINAL_HREF_PREFIX);
  });

  it("supports both formats in the same paragraph", () => {
    const out = transform("Legacy [c1] and modern [[2]] coexist.");
    expect(out).toContain(`(${CITATION_HREF_PREFIX}c1)`);
    expect(out).toContain(`(${CITATION_ORDINAL_HREF_PREFIX}2)`);
  });
});
