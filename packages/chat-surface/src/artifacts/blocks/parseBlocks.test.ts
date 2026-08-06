// What the scanner MODELS, as opposed to what it merely preserves.
//
// The round-trip suite proves no byte is lost whatever the classification is.
// This one pins the classification itself, because that is what decides whether
// a user gets a clickable cell or a paragraph of pipe characters — and, in the
// fence case, whether a code sample is mistaken for a grid.

import { describe, expect, it } from "vitest";

import {
  ALIGNED_TABLE,
  BUG_DOCUMENT,
  CRLF_DOCUMENT,
  EMPTY_CELLS_TABLE,
  EMPTY_HEADINGS,
  ESCAPED_PIPE_TABLE,
  FENCE_WITH_PIPES,
  FRONT_MATTER,
  HEADERLESS_PIPES,
  HEADING_EDGE_CASES,
  HTML_AND_INDENTED_CODE,
  LEADING_AND_TRAILING_BLANKS,
  LIST_AND_QUOTE,
  RAGGED_TABLE,
  SETEXT_AND_BREAK,
  TABLE_INTERRUPTING_PROSE,
  TABLE_SWALLOWED_BY_LIST,
  TIGHT_CONTAINERS,
} from "./corpus";
import { parseBlocks } from "./parseBlocks";
import type { DocumentBlock, TableBlock } from "./blockModel";

function kinds(source: string): string[] {
  return parseBlocks(source).map((block) =>
    block.kind === "raw" ? `raw:${block.reason}` : block.kind,
  );
}

function onlyTable(source: string): TableBlock {
  const table = parseBlocks(source).find(
    (block): block is TableBlock => block.kind === "table",
  );
  if (table === undefined) throw new Error("expected a table block");
  return table;
}

function rowTexts(table: TableBlock): string[][] {
  return table.rows.map((row) => row.map((cell) => cell.text));
}

describe("the document from the bug report", () => {
  const blocks: DocumentBlock[] = parseBlocks(BUG_DOCUMENT);

  it("reads as heading + table + prose, not as one string", () => {
    expect(kinds(BUG_DOCUMENT)).toEqual(["heading", "table", "paragraph"]);
  });

  it("exposes the heading's text without its marker", () => {
    const heading = blocks[0];
    if (heading.kind !== "heading") throw new Error("expected a heading");
    expect(heading.level).toBe(1);
    expect(heading.text).toBe("My Assigned Linear Issues");
  });

  it("keeps a cell's markdown link intact, which is the whole point", () => {
    const table = onlyTable(BUG_DOCUMENT);
    expect(table.headers).toEqual(["Issue", "Status", "Priority"]);
    expect(table.rows).toHaveLength(3);
    expect(table.rows[0][0].text).toBe(
      "[PAR-9 – Rent roll import errors](https://linear.app/parth/issue/PAR-9)",
    );
    expect(table.rows[2][2].text).toBe("Low");
  });

  it("keeps the trailing prose out of the table", () => {
    const paragraph = blocks[2];
    if (paragraph.kind !== "paragraph") throw new Error("expected a paragraph");
    expect(paragraph.text).toBe(
      "All five issues are currently in **Cool** status.",
    );
  });
});

describe("constructs that must NOT become tables", () => {
  it("leaves a code fence full of pipes as one raw block", () => {
    expect(kinds(FENCE_WITH_PIPES)).toEqual([
      "heading",
      "raw:fenced-code",
      "paragraph",
    ]);
    const fence = parseBlocks(FENCE_WITH_PIPES)[1];
    if (fence.kind !== "raw") throw new Error("expected a raw block");
    expect(fence.text).toContain("| --- |");
    expect(fence.text).toContain("| still code |");
  });

  it("routes setext headings and thematic breaks to raw", () => {
    expect(kinds(SETEXT_AND_BREAK)).toEqual([
      "raw:setext-heading",
      "raw:setext-heading",
      "raw:thematic-break",
      "paragraph",
    ]);
  });

  it("routes lists, quotes, html and indented code to raw", () => {
    expect(kinds(LIST_AND_QUOTE)).toEqual([
      "heading",
      "raw:list",
      "raw:blockquote",
      "paragraph",
    ]);
    expect(kinds(HTML_AND_INDENTED_CODE)).toEqual([
      "raw:html",
      "raw:indented-code",
      "paragraph",
    ]);
  });

  it("reads front matter the way the renderer does, not the way YAML does", () => {
    // No front-matter plugin is loaded (streamdown ships `gfm` + `codeMeta`), so
    // the user sees a rule, an H2 and a heading. Modelling YAML here would put
    // an editing affordance on something the render does not agree exists.
    expect(kinds(FRONT_MATTER)).toEqual([
      "raw:thematic-break",
      "raw:setext-heading",
      "heading",
      "paragraph",
    ]);
  });

  it("ends a container at a heading written with no blank line above it", () => {
    // The lazy-continuation trap: consuming to the next blank line would bury
    // both headings inside the quote and the list, where the renderer shows
    // them as headings.
    expect(kinds(TIGHT_CONTAINERS)).toEqual([
      "raw:blockquote",
      "heading",
      "raw:list",
      "heading",
      "paragraph",
    ]);
  });

  it("does NOT end a list at a table, which is the other half of that rule", () => {
    // A delimiter row interrupts an ordinary paragraph but not a lazy
    // continuation line, so this is one list and no table. Measured against
    // remark-gfm — it does not fall out of the CommonMark reading.
    expect(kinds(TABLE_SWALLOWED_BY_LIST)).toEqual(["raw:list"]);
  });
});

describe("table shapes", () => {
  it("treats an escaped pipe as cell content, not a delimiter", () => {
    const table = onlyTable(ESCAPED_PIPE_TABLE);
    expect(table.headers).toEqual(["Expression", "Meaning"]);
    expect(rowTexts(table)).toEqual([
      ["`a \\| b`", "bitwise or"],
      ["plain", "nothing \\| special"],
    ]);
  });

  it("keeps a ragged row's own cell count instead of padding it", () => {
    const table = onlyTable(RAGGED_TABLE);
    expect(table.rows.map((row) => row.length)).toEqual([3, 2, 4]);
    expect(rowTexts(table)[1]).toEqual(["4", "5"]);
  });

  it("gives an empty cell an empty span inside its own padding", () => {
    const table = onlyTable(EMPTY_CELLS_TABLE);
    expect(rowTexts(table)).toEqual([
      ["", ""],
      ["", "x"],
    ]);
    for (const cell of table.rows[0]) expect(cell.end).toBe(cell.start);
  });

  it("reads column alignment off the delimiter row", () => {
    expect(onlyTable(ALIGNED_TABLE).alignments).toEqual([
      "left",
      "center",
      "right",
    ]);
    expect(kinds(ALIGNED_TABLE)).toEqual(["paragraph", "table"]);
  });

  it("accepts a table written without outer pipes", () => {
    const table = onlyTable(HEADERLESS_PIPES);
    expect(table.headers).toEqual(["a", "b"]);
    expect(rowTexts(table)).toEqual([["1", "2"]]);
  });

  it("lets a table interrupt a paragraph, as remark-gfm does", () => {
    expect(kinds(TABLE_INTERRUPTING_PROSE)).toEqual(["paragraph", "table"]);
    const [prose] = parseBlocks(TABLE_INTERRUPTING_PROSE);
    if (prose.kind !== "paragraph") throw new Error("expected a paragraph");
    expect(prose.text).toBe("Here is the breakdown.");
  });

  it("survives CRLF line endings without swallowing the carriage return", () => {
    expect(kinds(CRLF_DOCUMENT)).toEqual(["heading", "table", "paragraph"]);
    const [heading, , tail] = parseBlocks(CRLF_DOCUMENT);
    if (heading.kind !== "heading") throw new Error("expected a heading");
    if (tail.kind !== "paragraph") throw new Error("expected a paragraph");
    expect(heading.text).toBe("Title");
    expect(tail.text).toBe("Tail.");
    expect(rowTexts(onlyTable(CRLF_DOCUMENT))).toEqual([["1", "2"]]);
  });
});

describe("heading edge cases", () => {
  it("names the separator a text-less heading's replacement needs", () => {
    // The zero-width span is fine; what is NOT fine is writing into it as if a
    // space were already there. Which side needs one is read off the source
    // rather than assumed, so a heading with room on both sides asks for
    // neither and one with room on neither asks for both.
    const separators = parseBlocks(EMPTY_HEADINGS).map((block) =>
      block.kind === "heading" ? block.separators : null,
    );
    expect(separators).toEqual([
      { before: " ", after: "" }, // `#`          — marker, then nothing
      { before: " ", after: "" }, // `######`     — the same at max depth
      { before: "", after: " " }, // `# ###`      — a space, then a closing run
      { before: "", after: " " }, // `###### ###`
      { before: "", after: "" }, // `#\t`        — a tab is a separator too
      { before: "", after: "" }, // `# Real heading`
    ]);
  });

  it("follows CommonMark on markers, closing runs and empty headings", () => {
    const blocks = parseBlocks(HEADING_EDGE_CASES);
    expect(blocks.map((block) => block.kind)).toEqual([
      "paragraph",
      "heading",
      "heading",
      "heading",
    ]);
    const [notAHeading, closed, deep, empty] = blocks;
    if (notAHeading.kind !== "paragraph") throw new Error("expected prose");
    if (closed.kind !== "heading") throw new Error("expected a heading");
    if (deep.kind !== "heading") throw new Error("expected a heading");
    if (empty.kind !== "heading") throw new Error("expected a heading");
    expect(notAHeading.text).toBe("#Not a heading");
    expect(closed.text).toBe("Closed heading");
    expect(deep.level).toBe(6);
    expect(deep.text).toBe("Deep");
    expect(empty.text).toBe("");
    expect(empty.textStart).toBe(empty.textEnd);
  });
});

describe("blank lines", () => {
  it("gives leading whitespace its own block and absorbs the rest", () => {
    expect(kinds(LEADING_AND_TRAILING_BLANKS)).toEqual([
      "raw:blank",
      "heading",
      "paragraph",
    ]);
    const [, heading, body] = parseBlocks(LEADING_AND_TRAILING_BLANKS);
    // The footprint runs to the next block; the editable span stops at the text.
    expect(LEADING_AND_TRAILING_BLANKS.slice(heading.start, heading.end)).toBe(
      "# Padded\n\n",
    );
    if (body.kind !== "paragraph") throw new Error("expected a paragraph");
    expect(body.text).toBe("Body.");
    expect(body.end).toBe(LEADING_AND_TRAILING_BLANKS.length);
  });

  it("parses an empty document as no blocks at all", () => {
    expect(parseBlocks("")).toEqual([]);
  });
});
