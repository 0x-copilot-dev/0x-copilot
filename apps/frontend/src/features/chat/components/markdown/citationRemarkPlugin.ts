// PR 1.1 — remark plugin that turns inline [c<id>] tokens into citation chips.
//
// We rewrite each token to an mdast `link` node with `href="#cite:<id>"`
// rather than introducing a new node type. The existing `MarkdownLink`
// component (registered as `components.a`) detects the `#cite:` prefix and
// renders a `CitationChip` — so the FE keeps a single inline-element
// component slot regardless of whether the rendered content is a link or
// a citation. Streaming-safe: only matches *closed* tokens, so partial
// `[c` / `[c3` chunks render as plain text until the closing `]` arrives.

import type { Plugin } from "unified";
import type { Root, Text, PhrasingContent, Parent } from "mdast";
import { visit } from "unist-util-visit";

const TOKEN_PATTERN = /\[c([0-9a-z]+)\]/g;
export const CITATION_HREF_PREFIX = "#cite:";

const SKIPPED_PARENT_TYPES = new Set([
  "link",
  "linkReference",
  "code",
  "inlineCode",
]);

export const remarkCitations: Plugin<[], Root> = () => {
  return (tree: Root) => {
    visit(
      tree,
      "text",
      (node: Text, index: number | undefined, parent: Parent | undefined) => {
        if (parent === undefined || index === undefined) {
          return;
        }
        // Don't render chips inside code spans, code fences, or existing
        // links — these contexts mean the bracketed text is intentional.
        if (SKIPPED_PARENT_TYPES.has(parent.type)) {
          return;
        }
        const replaced = splitCitationTokens(node.value);
        if (replaced === null) {
          return;
        }
        parent.children.splice(index, 1, ...replaced);
        return index + replaced.length;
      },
    );
  };
};

function splitCitationTokens(value: string): PhrasingContent[] | null {
  TOKEN_PATTERN.lastIndex = 0;
  if (!TOKEN_PATTERN.test(value)) {
    return null;
  }
  TOKEN_PATTERN.lastIndex = 0;
  const out: PhrasingContent[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = TOKEN_PATTERN.exec(value)) !== null) {
    if (match.index > cursor) {
      out.push({ type: "text", value: value.slice(cursor, match.index) });
    }
    const id = `c${match[1]}`;
    out.push({
      type: "link",
      url: `${CITATION_HREF_PREFIX}${id}`,
      title: null,
      children: [{ type: "text", value: match[0] }],
    });
    cursor = match.index + match[0].length;
  }
  if (cursor < value.length) {
    out.push({ type: "text", value: value.slice(cursor) });
  }
  return out;
}
