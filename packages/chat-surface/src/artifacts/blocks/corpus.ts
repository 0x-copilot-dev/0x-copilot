// The document corpus the block tests run over. Test data only — deliberately
// not on the `blocks/` barrel and not imported by product code.
//
// `BUG_DOCUMENT` is the document from the complaint that started this work: a
// heading, a table whose cells hold Linear links, and a trailing paragraph of
// prose. It is the case that proves "splice, never regenerate" is not a style
// preference — a parse-and-re-emit round trip is exactly what would flatten
// those links or reflow that sentence.

export const BUG_DOCUMENT = `# My Assigned Linear Issues

| Issue | Status | Priority |
| --- | --- | --- |
| [PAR-9 – Rent roll import errors](https://linear.app/parth/issue/PAR-9) | Cool | High |
| [PAR-12 – Sync retry backoff](https://linear.app/parth/issue/PAR-12) | Cool | Medium |
| [PAR-14 – Webhook replay gap](https://linear.app/parth/issue/PAR-14) | Cool | Low |

All five issues are currently in **Cool** status.
`;

export const FENCE_WITH_PIPES = `## Query

\`\`\`sql
SELECT a | b FROM t
| --- |
| still code |
\`\`\`

Prose under the fence.
`;

export const ESCAPED_PIPE_TABLE = `| Expression | Meaning |
| --- | --- |
| \`a \\| b\` | bitwise or |
| plain | nothing \\| special |
`;

export const RAGGED_TABLE = `| A | B | C |
| --- | --- | --- |
| 1 | 2 | 3 |
| 4 | 5 |
| 6 | 7 | 8 | 9 |
`;

export const EMPTY_CELLS_TABLE = `| A | B |
|---|---|
|  |  |
||x|
`;

export const ALIGNED_TABLE = `Sales by region

| Region | Units | Revenue |
| :--- | :---: | ---: |
| EMEA | 12 | 4,300 |
`;

export const HEADERLESS_PIPES = `a | b
--- | ---
1 | 2
`;

export const TABLE_INTERRUPTING_PROSE = `Here is the breakdown.
| A | B |
| --- | --- |
| 1 | 2 |
`;

export const LIST_AND_QUOTE = `# Notes

- first item
- second item
  with a continuation

> a quoted line
> and another

Closing prose.
`;

export const SETEXT_AND_BREAK = `Setext title
===

Second title
---

***

Body text.
`;

/**
 * Deliberately NOT modelled as front matter: the renderer loads no front-matter
 * plugin, so it shows a rule, an H2 and a heading. The block model agrees with
 * the render rather than with YAML.
 */
export const FRONT_MATTER = `---
title: Weekly report
tags: [ops, billing]
---

# Weekly report

Body.
`;

/**
 * Headings written with no blank line above them, inside a quote and a list —
 * the lazy-continuation trap. Consuming a container to the next blank line
 * buries both headings in a raw block the renderer disagrees with.
 */
export const TIGHT_CONTAINERS = `> quoted line
# Heading after a quote

- first
- second
# Heading after a list

Tail.
`;

/**
 * The asymmetry that has to be measured rather than reasoned about: a delimiter
 * row interrupts an ordinary paragraph (see `TABLE_INTERRUPTING_PROSE`) but NOT
 * a list's lazy continuation line, so this is one list and no table at all.
 */
export const TABLE_SWALLOWED_BY_LIST = `- first
- second
| A | B |
| --- | --- |
| 1 | 2 |
`;

export const HTML_AND_INDENTED_CODE = `<div class="callout">
  <p>Raw HTML block</p>
</div>

    indented code
    second line

Tail paragraph.
`;

export const HEADING_EDGE_CASES = `#Not a heading

# Closed heading ###

###### Deep

######
`;

export const CRLF_DOCUMENT =
  "# Title\r\n\r\n| A | B |\r\n| --- | --- |\r\n| 1 | 2 |\r\n\r\nTail.\r\n";

export const NO_TRAILING_NEWLINE = `# Title

| A | B |
| --- | --- |
| 1 | 2 |`;

export const UNCLOSED_FENCE = `Intro

\`\`\`js
const a = 1;
`;

export const LEADING_AND_TRAILING_BLANKS = `

# Padded

Body.


`;

export const ONLY_WHITESPACE = "\n \n\t\n";

export const EMPTY = "";

/** Every document above, keyed for table-driven tests. */
export const MARKDOWN_CORPUS: Readonly<Record<string, string>> = {
  BUG_DOCUMENT,
  FENCE_WITH_PIPES,
  ESCAPED_PIPE_TABLE,
  RAGGED_TABLE,
  EMPTY_CELLS_TABLE,
  ALIGNED_TABLE,
  HEADERLESS_PIPES,
  TABLE_INTERRUPTING_PROSE,
  LIST_AND_QUOTE,
  TIGHT_CONTAINERS,
  TABLE_SWALLOWED_BY_LIST,
  SETEXT_AND_BREAK,
  FRONT_MATTER,
  HTML_AND_INDENTED_CODE,
  HEADING_EDGE_CASES,
  CRLF_DOCUMENT,
  NO_TRAILING_NEWLINE,
  UNCLOSED_FENCE,
  LEADING_AND_TRAILING_BLANKS,
  ONLY_WHITESPACE,
  EMPTY,
};

/**
 * Fragments the generative round-trip test shuffles into synthetic documents.
 * Each one is a whole construct; the generator only decides the order and how
 * many blank lines separate them, which is where a scanner's coverage bugs live.
 */
export const CORPUS_FRAGMENTS: readonly string[] = [
  "# Heading one",
  "###### Heading six ###",
  "Plain prose with **bold** and a [link](https://example.com/a|b).",
  "Two-line\nparagraph body.",
  "| A | B |\n| --- | --- |\n| 1 | 2 |",
  "| Only |\n| :-: |\n| one |",
  "| A | B |\n| --- | --- |\n| 1 |",
  "no | outer | pipes\n--- | --- | ---\n1 | 2 | 3",
  "```\ncode | with | pipes\n| --- |\n```",
  "~~~py\nx = 1\n~~~",
  "    indented code",
  "- a\n- b",
  "1. one\n2. two",
  "> quoted",
  "> quoted\n# tight heading",
  "- a\n- b\n| A | B |\n| --- | --- |\n| 1 | 2 |",
  "<div>\nhtml\n</div>",
  "<!-- a comment\nspanning lines -->",
  "---",
  "***",
  "Setext\n===",
  "| Escaped \\| pipe | b |\n| --- | --- |\n| x \\| y | z |",
  "|  |  |\n| --- | --- |\n|  | x |",
];
