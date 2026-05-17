// Remark plugin that turns inline citation tokens into citation chips.
//
// Two token formats are recognized:
//
//   - PR 1.1 (legacy)  `[c<base36>]`  — per-run citation_id from the
//     CitationLedger. Resolves against the per-run citation registry.
//   - PR 1.1-rev2      `[[<int>]]`    — conversation_ordinal of a tool
//     invocation. Resolves against the tool invocation registry by
//     ordinal. Stable across turns (cross-turn citation works).
//
// We rewrite each match to an mdast `link` node so the existing
// MarkdownLink component (registered as `components.a`) can detect the
// prefix and render the appropriate chip — the FE keeps a single
// inline-element component slot regardless of which token format is in
// flight. Streaming-safe: only *closed* tokens match, so partial
// `[c` / `[c3` / `[[` / `[[4` chunks render as plain text until the
// closing bracket arrives.
//
// The two href prefixes exported below are the single source of truth
// shared between this emitter and the chip components that parse them.
// Drift between emitter and parser would silently break citation
// rendering, so they live in exactly one place.

import type { Parent, PhrasingContent, Root, Text } from "mdast";
import type { Plugin } from "unified";
import { visit } from "unist-util-visit";

export const CITATION_HREF_PREFIX = "#cite:";
export const CITATION_ORDINAL_HREF_PREFIX = "#cite-ord:";

// Combined pattern: matches either `[c<base36>]` or `[[<digits>]]`.
// The two captures are mutually exclusive — exactly one of group 1
// (legacy id) and group 2 (conversation_ordinal) is non-undefined per
// match, which the rewriter uses to pick the right href prefix.
const TOKEN_PATTERN = /\[c([0-9a-z]+)\]|\[\[(\d+)\]\]/g;

const SKIPPED_PARENT_TYPES = new Set([
  "link",
  "linkReference",
  "code",
  "inlineCode",
]);

export interface RemarkCitationsOptions {
  /**
   * Fired once per text node that contained one or more citation tokens,
   * with the raw matched tokens (e.g. `["[c3]", "[[7]]"]`). Optional —
   * the plugin's rewriting behavior is unchanged when absent. Provided
   * as an injection point so callers can wire diagnostics (a logger, a
   * counter, …) without coupling the plugin to a substrate-specific
   * sink.
   */
  readonly onMatch?: (matches: readonly string[]) => void;
}

/**
 * Build a remark plugin instance. Most callers will share a single
 * instance constructed at module load time:
 *
 * ```ts
 * const remarkCitations = createRemarkCitations({ onMatch: log });
 * ```
 *
 * A factory (rather than a const plugin) lets callers inject the
 * `onMatch` callback while keeping the plugin module itself
 * substrate-agnostic.
 */
export function createRemarkCitations(
  options: RemarkCitationsOptions = {},
): Plugin<[], Root> {
  const onMatch = options.onMatch;
  return () => (tree: Root) => {
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
        const replaced = splitCitationTokens(node.value, onMatch);
        if (replaced === null) {
          return;
        }
        parent.children.splice(index, 1, ...replaced);
        return index + replaced.length;
      },
    );
  };
}

function splitCitationTokens(
  value: string,
  onMatch: ((matches: readonly string[]) => void) | undefined,
): PhrasingContent[] | null {
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
      // PR 1.1-rev2 — strip leading zeros so the href and the resolver
      // agree on the canonical ordinal form.
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
    onMatch?.(matches);
  }
  return out;
}
