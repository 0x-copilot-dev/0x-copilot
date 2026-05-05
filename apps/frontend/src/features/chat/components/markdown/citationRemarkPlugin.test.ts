import { describe, expect, it } from "vitest";
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkStringify from "remark-stringify";
import { CITATION_HREF_PREFIX, remarkCitations } from "./citationRemarkPlugin";

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
});
