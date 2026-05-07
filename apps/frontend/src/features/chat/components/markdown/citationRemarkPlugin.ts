// Remark plugin that turns inline citation tokens into citation chips.
//
// Two token formats are recognized:
//
// - PR 1.1 (legacy)   `[c<base36>]`  — per-run citation_id from the
//   ``CitationLedger``. Resolves against the per-run citation registry.
// - PR 1.1-rev2       `[[<int>]]`    — conversation_ordinal of a tool
//   invocation. Resolves against the tool invocation registry by
//   ordinal. Stable across turns (cross-turn citation works).
//
// We rewrite each match to an mdast `link` node so the existing
// ``MarkdownLink`` component (registered as `components.a`) can detect
// the prefix and render a ``CitationChip`` — the FE keeps a single
// inline-element component slot regardless of which token format is in
// flight. Streaming-safe: only *closed* tokens match, so partial
// ``[c`` / ``[c3`` / ``[[`` / ``[[4`` chunks render as plain text until
// the closing bracket arrives.

import type { Plugin } from "unified";
import type { Root, Text, PhrasingContent, Parent } from "mdast";
import { visit } from "unist-util-visit";
import { citationDebug } from "../../chatModel/citationDebug";

// Combined pattern: matches either ``[c<base36>]`` or ``[[<digits>]]``.
// The two captures are mutually exclusive — exactly one of group 1
// (legacy id) and group 2 (conversation_ordinal) is non-undefined per
// match, which the rewriter uses to pick the right href prefix.
const TOKEN_PATTERN = /\[c([0-9a-z]+)\]|\[\[(\d+)\]\]/g;
export const CITATION_HREF_PREFIX = "#cite:";
// PR 1.1-rev2 — separate prefix so MarkdownLink can dispatch by ordinal.
export const CITATION_ORDINAL_HREF_PREFIX = "#cite-ord:";

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
  const matches: string[] = [];
  const out: PhrasingContent[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = TOKEN_PATTERN.exec(value)) !== null) {
    matches.push(match[0]);
    if (match.index > cursor) {
      out.push({ type: "text", value: value.slice(cursor, match.index) });
    }
    const legacyId = match[1];
    const ordinalText = match[2];
    let url: string;
    if (legacyId !== undefined) {
      url = `${CITATION_HREF_PREFIX}c${legacyId}`;
    } else {
      // PR 1.1-rev2 — strip leading zeros so the href and the
      // resolver agree on the canonical ordinal form.
      const ordinal = String(parseInt(ordinalText ?? "0", 10));
      url = `${CITATION_ORDINAL_HREF_PREFIX}${ordinal}`;
    }
    out.push({
      type: "link",
      url,
      title: null,
      children: [{ type: "text", value: match[0] }],
    });
    cursor = match.index + match[0].length;
  }
  if (cursor < value.length) {
    out.push({ type: "text", value: value.slice(cursor) });
  }
  if (matches.length > 0) {
    citationDebug(`plugin.match tokens=${matches.length}`, matches);
  }
  return out;
}
