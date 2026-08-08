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

/**
 * A table with no outer pipes whose cells are the ones that stop being cells the
 * moment the pipes around them go.
 *
 * Every line here is a row while the pipes hold it together, and three of them
 * are one character away from being something else entirely: `spare | ---` loses
 * its opening and leaves `---`, a thematic break; `gateway | -` leaves ` -`, a
 * list; `--- | x` keeps its opening but loses its only pipe when the LAST column
 * goes, and `---` alone is a thematic break again. Each one ends the table where
 * it stands and turns every row below it into pipe-noise in a paragraph — which
 * is why the corpus properties, which reparse and compare block kinds, are what
 * hold this shut rather than a string comparison. A lone `-` meaning "no value"
 * is among the commonest cells a real data table has; none of this is exotic.
 */
export const PIPELESS_PLACEHOLDERS = `Item | Note
--- | ---
a | 1
spare | ---
gateway | -
--- | x

Everything above is one table.
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

/**
 * Headings with NO text, which is where the editable span is zero-width and the
 * byte on each side of it is a `#`.
 *
 * These are the documents that proved "splice, never regenerate" can be held to
 * the letter and still corrupt a document: writing `Summary` into `######`
 * produced `######Summary`, which reparses as a PARAGRAPH, and writing it into
 * `# ###` produced `# Summary###`, a heading whose text now ends in the closing
 * run. Both are one span replacement with every other byte identical — and both
 * changed something the user never named. Every variant is here because the
 * generic properties in `roundTrip.test.ts` are what cover them, not a
 * hand-written example per case.
 */
export const EMPTY_HEADINGS = `#

######

# ###

###### ###

#\t

# Real heading
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
  PIPELESS_PLACEHOLDERS,
  TABLE_INTERRUPTING_PROSE,
  LIST_AND_QUOTE,
  TIGHT_CONTAINERS,
  TABLE_SWALLOWED_BY_LIST,
  SETEXT_AND_BREAK,
  FRONT_MATTER,
  HTML_AND_INDENTED_CODE,
  HEADING_EDGE_CASES,
  EMPTY_HEADINGS,
  CRLF_DOCUMENT,
  NO_TRAILING_NEWLINE,
  UNCLOSED_FENCE,
  LEADING_AND_TRAILING_BLANKS,
  ONLY_WHITESPACE,
  EMPTY,
};

/**
 * Fragments the generative round-trip tests shuffle into synthetic documents.
 * Each one is a whole construct; the generator only decides the order and how
 * many blank lines separate them, which is where a scanner's coverage bugs live.
 */
export const CORPUS_FRAGMENTS: readonly string[] = [
  "# Heading one",
  "###### Heading six ###",
  "#",
  "######",
  "# ###",
  "Plain prose with **bold** and a [link](https://example.com/a|b).",
  "Two-line\nparagraph body.",
  "| A | B |\n| --- | --- |\n| 1 | 2 |",
  "| Only |\n| :-: |\n| one |",
  "| A | B |\n| --- | --- |\n| 1 |",
  "no | outer | pipes\n--- | --- | ---\n1 | 2 | 3",
  // Cells that stop being cells once the pipes around them go — see
  // `PIPELESS_PLACEHOLDERS`.
  "Item | Note\n--- | ---\nspare | ---\ngateway | -\n--- | x",
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

/**
 * Deterministic PRNG, so a failing generated document is reproducible from its
 * seed rather than from a lucky re-run.
 */
export function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const SEPARATORS = ["\n\n", "\n", "\n\n\n", "\n \n"];

/**
 * Shuffles `CORPUS_FRAGMENTS` into one synthetic document.
 *
 * The generator decides three things only — which constructs, in what order, and
 * how much blank space between them — because those are what a hand-written
 * example cannot cover. `"\n"` is in the separator list on purpose: two
 * constructs with NO blank line between them is where a paragraph swallows the
 * block under it and a container lazily continues into the next one.
 */
export function generateDocument(random: () => number): string {
  const count = 1 + Math.floor(random() * 6);
  let document = random() < 0.1 ? "\n\n" : "";
  for (let index = 0; index < count; index += 1) {
    const fragment =
      CORPUS_FRAGMENTS[Math.floor(random() * CORPUS_FRAGMENTS.length)];
    document += fragment;
    document += SEPARATORS[Math.floor(random() * SEPARATORS.length)];
  }
  return random() < 0.2 ? document.trimEnd() : document;
}

/**
 * Single LINES, for the differential suite's near-miss generator.
 *
 * `CORPUS_FRAGMENTS` above are whole constructs, and that is what makes them
 * blind to the failure this alphabet exists to find: a construct is decided by
 * the line UNDER it, so a scanner and a renderer disagree at the seam between two
 * lines that each look unambiguous alone. `a | b` is prose or a header row
 * depending on what follows; `- | ---` is a delimiter row to a permissive
 * scanner and a bullet to remark-gfm. Crossing the alphabet with itself is what
 * puts every such pair in front of the oracle.
 *
 * Grouped by what they are one character away from being.
 */
export const NEAR_MISS_LINES: readonly string[] = [
  // prose, and prose that could be a header row
  "a",
  "a | b",
  "| a | b |",
  "| a |b|",
  "a \\| b",
  "**a** | b",
  "a|",
  "|a",
  "1 | 2",
  // near-heading
  "#a",
  "# a",
  "# a #",
  "#",
  "####### a",
  "   # a",
  "    # a",
  // near-delimiter-row
  "---",
  "--- | ---",
  "| --- | --- |",
  "|---|---|",
  "- | ---",
  "-- | --",
  "-|-",
  "| - |",
  "| -",
  "- |",
  ":--- | ---:",
  "--:|:--",
  "-- -",
  "\\| | ---",
  "***",
  "___",
  "===",
  // near-list
  "-",
  "- x",
  "* x",
  "+ x",
  "1. x",
  "2. x",
  "1) x",
  "  - x",
  "    - x",
  // containers and code
  "> q",
  ">",
  "    code",
  "```",
  "```js",
  "~~~",
  // html, types 6 and 7 — the pair that decides whether a paragraph continues
  "<div>",
  "</div>",
  "<!-- c -->",
  "<br/>",
  "<span>x</span>",
  // blanks
  "",
  " ",
];

/**
 * Every ordered pair of `NEAR_MISS_LINES`, as a two-line document.
 *
 * Exhaustive rather than sampled: the alphabet is small enough that the whole
 * cross product costs less than a generator that might miss the one pair that
 * matters.
 */
export function nearMissPairs(): string[] {
  const documents: string[] = [];
  for (const first of NEAR_MISS_LINES) {
    for (const second of NEAR_MISS_LINES) {
      documents.push(`${first}\n${second}\n`);
    }
  }
  return documents;
}

/** A document of `lineCount` lines drawn from `NEAR_MISS_LINES`. */
export function generateNearMissDocument(
  random: () => number,
  lineCount = 4,
): string {
  let document = "";
  for (let index = 0; index < lineCount; index += 1) {
    document +=
      NEAR_MISS_LINES[Math.floor(random() * NEAR_MISS_LINES.length)] + "\n";
  }
  return random() < 0.2 ? document.trimEnd() : document;
}
