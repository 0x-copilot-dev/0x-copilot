// Structural edits: the document REPARSES into what was intended.
//
// "The string changed" is the assertion that lets a structural edit destroy a
// document. A header row that gained a column while its delimiter row did not is
// a string that changed and a table that no longer exists — it reparses as
// prose, and nothing about the diff says so. So every test here applies the
// edits and then READS THE RESULT BACK, asserting the block kinds and the
// table's shape, and the corpus-wide properties re-run the coverage round trip
// over the document each operation produced.
//
// Three of those assertions are one line each and worth naming, because each one
// is a trap that has to be checked on the far side of the edit:
//
//   * the delimiter row's alignment count must still equal the header's, which
//     `parseBlocks` reports as "this is a table" and otherwise as two
//     paragraphs;
//   * the document's final newline must be exactly as present as it was;
//   * CRLF documents must not gain a bare `\n`.

import { describe, expect, it } from "vitest";

import {
  ALIGNED_TABLE,
  BUG_DOCUMENT,
  CRLF_DOCUMENT,
  EMPTY_CELLS_TABLE,
  ESCAPED_PIPE_TABLE,
  generateDocument,
  HEADERLESS_PIPES,
  MARKDOWN_CORPUS,
  mulberry32,
  NO_TRAILING_NEWLINE,
  RAGGED_TABLE,
} from "./corpus";
import { parseBlocks } from "./parseBlocks";
import { applyEdits, blockEdit, cellEdit, spliceCell } from "./splice";
import {
  addBlockEdits,
  addColumnEdits,
  addRowEdits,
  deleteBlockEdits,
  deleteColumnEdits,
  deleteRowEdits,
  swapBlocksEdits,
} from "./structure";
import type { ColumnAlignment, DocumentBlock, TableBlock } from "./blockModel";

const TRAILING_NEWLINE = /(?:\r\n|\n|\r)$/;

/** A heading, because it is the one block that cannot merge with a neighbour. */
const INSERTED = "## Inserted";

function tablesOf(source: string): TableBlock[] {
  return parseBlocks(source).filter(
    (block): block is TableBlock => block.kind === "table",
  );
}

function tableOf(source: string): TableBlock {
  const table = tablesOf(source)[0];
  if (table === undefined) throw new Error("expected a table block");
  return table;
}

function kindOf(block: DocumentBlock): string {
  return block.kind === "raw" ? `raw:${block.reason}` : block.kind;
}

function blockKinds(source: string): string[] {
  return parseBlocks(source).map(kindOf);
}

interface TableShape {
  readonly headers: readonly string[];
  readonly alignments: readonly ColumnAlignment[];
  /** Cells per body row — ragged rows keep their own count. */
  readonly rows: readonly number[];
  readonly cells: readonly (readonly string[])[];
}

function tableShapes(source: string): TableShape[] {
  return tablesOf(source).map((table) => ({
    headers: table.headers,
    alignments: table.alignments,
    rows: table.rows.map((row) => row.length),
    cells: table.rows.map((row) => row.map((cell) => cell.text)),
  }));
}

/** One block's kind and its content, with the blank run it absorbed trimmed off. */
interface BlockSignature {
  readonly kind: string;
  readonly text: string;
}

function signatures(source: string): BlockSignature[] {
  return parseBlocks(source).map((block) => ({
    kind: kindOf(block),
    text: source.slice(block.start, block.end).trimEnd(),
  }));
}

/**
 * Blocks with content, which is what a reorder or a deletion is about.
 *
 * A run of blank lines at the top of a document is a block, and any operation
 * that puts a block above it absorbs it — blocks own their trailing blank lines,
 * so the run stops being a block of its own without a byte of content moving.
 * Comparing contentful blocks says what actually happened to the document
 * instead of counting whitespace.
 */
function contentful(list: readonly BlockSignature[]): BlockSignature[] {
  return list.filter((entry) => entry.text.length > 0);
}

function hasContent(source: string, block: DocumentBlock): boolean {
  return source.slice(block.start, block.end).trim().length > 0;
}

/**
 * The properties every structural edit holds whatever it did: the block model
 * still covers the result byte for byte, and the document's final newline is
 * exactly as present as it was.
 */
function assertDocumentIntact(
  before: string,
  after: string,
  label: string,
): void {
  const blocks = parseBlocks(after);
  let cursor = 0;
  for (const block of blocks) {
    if (block.start !== cursor || block.end <= block.start) {
      throw new Error(
        `${label}: blocks are not contiguous over ${JSON.stringify(after)}`,
      );
    }
    cursor = block.end;
  }
  if (cursor !== after.length) {
    throw new Error(
      `${label}: blocks do not cover ${JSON.stringify(after)} (stopped at ${cursor})`,
    );
  }
  const rejoined = blocks.map((block) => after.slice(block.start, block.end));
  if (rejoined.join("") !== after) {
    throw new Error(
      `${label}: round trip lost bytes:\n${JSON.stringify(after)}\n!=\n${JSON.stringify(rejoined.join(""))}`,
    );
  }
  // An empty result has no final newline to preserve, and that is the point:
  // deleting the only block of a document leaves `""`, the empty document — not
  // `"\n"`, which is a document holding one blank line.
  if (
    after.length > 0 &&
    TRAILING_NEWLINE.test(before) !== TRAILING_NEWLINE.test(after)
  ) {
    throw new Error(
      `${label}: the document's final newline changed:\n${JSON.stringify(before)}\n->\n${JSON.stringify(after)}`,
    );
  }
}

// --- rows -------------------------------------------------------------------

describe("addRowEdits", () => {
  it("appends an empty row above the blank line, and the row is editable", () => {
    const next = applyEdits(
      BUG_DOCUMENT,
      addRowEdits(BUG_DOCUMENT, tableOf(BUG_DOCUMENT)),
    );
    expect(next).toContain(
      "| [PAR-14 – Webhook replay gap](https://linear.app/parth/issue/PAR-14) | Cool | Low |\n|  |  |  |\n\nAll five issues",
    );

    const table = tableOf(next);
    expect(table.rows).toHaveLength(4);
    expect(table.rows[3].map((cell) => cell.text)).toEqual(["", "", ""]);
    // The two spaces per cell are what make the empty span splice to `| x |`.
    expect(spliceCell(next, table, 3, 0, "PAR-20")).toContain(
      "| PAR-20 |  |  |",
    );
  });

  it("takes its column count from the header, not from a ragged last row", () => {
    const next = applyEdits(
      RAGGED_TABLE,
      addRowEdits(RAGGED_TABLE, tableOf(RAGGED_TABLE)),
    );
    expect(next.endsWith("| 6 | 7 | 8 | 9 |\n|  |  |  |\n")).toBe(true);
    expect(tableShapes(next)[0].rows).toEqual([3, 2, 4, 3]);
  });

  it("adds a body to a table that has only a header and a delimiter", () => {
    const source = "| A | B |\n| --- | --- |\n\nTail.\n";
    const next = applyEdits(source, addRowEdits(source, tableOf(source)));
    expect(next).toBe("| A | B |\n| --- | --- |\n|  |  |\n\nTail.\n");
    expect(blockKinds(next)).toEqual(["table", "paragraph"]);
  });

  it("writes the document's own line ending", () => {
    const next = applyEdits(
      CRLF_DOCUMENT,
      addRowEdits(CRLF_DOCUMENT, tableOf(CRLF_DOCUMENT)),
    );
    expect(next).toContain("| 1 | 2 |\r\n|  |  |\r\n\r\nTail.");
    expect(next.replace(/\r\n/g, "")).not.toMatch(/[\r\n]/);
  });

  it("does not give a document without a final newline one", () => {
    const next = applyEdits(
      NO_TRAILING_NEWLINE,
      addRowEdits(NO_TRAILING_NEWLINE, tableOf(NO_TRAILING_NEWLINE)),
    );
    expect(next).toBe(`${NO_TRAILING_NEWLINE}\n|  |  |`);
    expect(next.endsWith("\n")).toBe(false);
    expect(tableShapes(next)[0].rows).toEqual([2, 2]);
  });

  it("copies the indentation of the row above it", () => {
    const source = "  | A | B |\n  | --- | --- |\n  | 1 | 2 |\n";
    const next = applyEdits(source, addRowEdits(source, tableOf(source)));
    expect(next).toBe("  | A | B |\n  | --- | --- |\n  | 1 | 2 |\n  |  |  |\n");
    expect(blockKinds(next)).toEqual(["table"]);
  });

  it("writes the canonical piped row into a table that has no outer pipes", () => {
    // A pipe-less row of empty cells trims to a bare `|`, which is not a row at
    // all — the piped form is the only one an empty row has.
    const next = applyEdits(
      HEADERLESS_PIPES,
      addRowEdits(HEADERLESS_PIPES, tableOf(HEADERLESS_PIPES)),
    );
    expect(next).toBe("a | b\n--- | ---\n1 | 2\n|  |  |\n");
    expect(tableShapes(next)[0].rows).toEqual([2, 2]);
  });
});

describe("deleteRowEdits", () => {
  it("takes the row's whole line, terminator included", () => {
    const next = applyEdits(
      BUG_DOCUMENT,
      deleteRowEdits(BUG_DOCUMENT, tableOf(BUG_DOCUMENT), 1),
    );
    expect(next).toBe(
      BUG_DOCUMENT.replace(
        "| [PAR-12 – Sync retry backoff](https://linear.app/parth/issue/PAR-12) | Cool | Medium |\n",
        "",
      ),
    );
    expect(tableShapes(next)[0].cells).toEqual([
      [
        "[PAR-9 – Rent roll import errors](https://linear.app/parth/issue/PAR-9)",
        "Cool",
        "High",
      ],
      [
        "[PAR-14 – Webhook replay gap](https://linear.app/parth/issue/PAR-14)",
        "Cool",
        "Low",
      ],
    ]);
  });

  it("leaves a header and a delimiter when the last row goes, which is still a table", () => {
    const source = "| A | B |\n| --- | --- |\n| 1 | 2 |\n\nTail.\n";
    const next = applyEdits(source, deleteRowEdits(source, tableOf(source), 0));
    expect(next).toBe("| A | B |\n| --- | --- |\n\nTail.\n");
    expect(blockKinds(next)).toEqual(["table", "paragraph"]);
    expect(tableShapes(next)[0]).toMatchObject({
      headers: ["A", "B"],
      alignments: [null, null],
      rows: [],
    });
  });

  it("takes the previous terminator when the row ends a document that has none", () => {
    // Removing only `[start, end)` would strand the delimiter row's newline and
    // the document would end with one it never had.
    const next = applyEdits(
      NO_TRAILING_NEWLINE,
      deleteRowEdits(NO_TRAILING_NEWLINE, tableOf(NO_TRAILING_NEWLINE), 0),
    );
    expect(next).toBe("# Title\n\n| A | B |\n| --- | --- |");
    expect(next.endsWith("\n")).toBe(false);
    expect(blockKinds(next)).toEqual(["heading", "table"]);
  });

  it("refuses a row the table does not have", () => {
    const table = tableOf(BUG_DOCUMENT);
    expect(() => deleteRowEdits(BUG_DOCUMENT, table, 3)).toThrow(RangeError);
    expect(() => deleteRowEdits(BUG_DOCUMENT, table, -1)).toThrow(RangeError);
  });
});

// --- columns ----------------------------------------------------------------

describe("addColumnEdits", () => {
  it("widens the header AND the delimiter row in one batch", () => {
    const next = applyEdits(
      BUG_DOCUMENT,
      addColumnEdits(BUG_DOCUMENT, tableOf(BUG_DOCUMENT)),
    );
    expect(next).toContain("| Issue | Status | Priority |  |\n");
    expect(next).toContain("| --- | --- | --- | --- |\n");
    expect(next).toContain("| Cool | High |  |\n");
    // The assertion that matters: it is still a table. A header of four cells
    // over a delimiter of three reparses as two paragraphs.
    expect(blockKinds(next)).toEqual(["heading", "table", "paragraph"]);
    expect(tableShapes(next)[0]).toMatchObject({
      headers: ["Issue", "Status", "Priority", ""],
      alignments: [null, null, null, null],
      rows: [4, 4, 4],
    });
  });

  it("copies the alignment marker of the column it joins", () => {
    const next = applyEdits(
      ALIGNED_TABLE,
      addColumnEdits(ALIGNED_TABLE, tableOf(ALIGNED_TABLE)),
    );
    expect(next).toContain("| :--- | :---: | ---: | ---: |");
    expect(tableShapes(next)[0].alignments).toEqual([
      "left",
      "center",
      "right",
      "right",
    ]);
  });

  it("copies a delimiter written without padding, dashes and all", () => {
    const next = applyEdits(
      EMPTY_CELLS_TABLE,
      addColumnEdits(EMPTY_CELLS_TABLE, tableOf(EMPTY_CELLS_TABLE)),
    );
    expect(next).toBe("| A | B |  |\n|---|---| --- |\n|  |  |  |\n||x|  |\n");
    expect(tableShapes(next)[0]).toMatchObject({
      headers: ["A", "B", ""],
      alignments: [null, null, null],
      rows: [3, 3],
    });
  });

  it("gives a row without outer pipes one, because a trailing empty cell needs it", () => {
    const next = applyEdits(
      HEADERLESS_PIPES,
      addColumnEdits(HEADERLESS_PIPES, tableOf(HEADERLESS_PIPES)),
    );
    expect(next).toBe("a | b |  |\n--- | --- | --- |\n1 | 2 |  |\n");
    expect(tableShapes(next)[0]).toMatchObject({
      headers: ["a", "b", ""],
      rows: [3],
    });
  });

  it("gives every ragged row a cell at its OWN end", () => {
    const next = applyEdits(
      RAGGED_TABLE,
      addColumnEdits(RAGGED_TABLE, tableOf(RAGGED_TABLE)),
    );
    expect(next).toBe(
      [
        "| A | B | C |  |",
        "| --- | --- | --- | --- |",
        "| 1 | 2 | 3 |  |",
        "| 4 | 5 |  |",
        "| 6 | 7 | 8 | 9 |  |",
        "",
      ].join("\n"),
    );
    expect(tableShapes(next)[0].rows).toEqual([4, 3, 5]);
  });

  it("skips a row that has no cells to append after", () => {
    // A bare `|` is a row of zero cells. There is no end to append to, and
    // inventing one would turn it into a one-cell row nobody wrote.
    const source = "| A | B |\n| --- | --- |\n|\n| 1 | 2 |\n";
    const next = applyEdits(source, addColumnEdits(source, tableOf(source)));
    expect(next).toBe("| A | B |  |\n| --- | --- | --- |\n|\n| 1 | 2 |  |\n");
    expect(tableShapes(next)[0].rows).toEqual([0, 3]);
  });

  it("leaves an escaped pipe escaped", () => {
    const next = applyEdits(
      ESCAPED_PIPE_TABLE,
      addColumnEdits(ESCAPED_PIPE_TABLE, tableOf(ESCAPED_PIPE_TABLE)),
    );
    expect(next).toContain("| `a \\| b` | bitwise or |  |");
    expect(next).toContain("| plain | nothing \\| special |  |");
    expect(tableShapes(next)[0].rows).toEqual([3, 3]);
  });
});

describe("deleteColumnEdits", () => {
  it("removes the cell and one delimiter from every row, prose untouched", () => {
    const next = applyEdits(
      BUG_DOCUMENT,
      deleteColumnEdits(BUG_DOCUMENT, tableOf(BUG_DOCUMENT), 1),
    );
    expect(next).toContain("| Issue | Priority |\n| --- | --- |\n");
    expect(next).toContain(
      "| [PAR-9 – Rent roll import errors](https://linear.app/parth/issue/PAR-9) | High |",
    );
    // The sentence still says Cool: the edit reached the table and nothing else.
    expect(next).toContain("All five issues are currently in **Cool** status.");
    expect(tableShapes(next)[0]).toMatchObject({
      headers: ["Issue", "Priority"],
      alignments: [null, null],
      rows: [2, 2, 2],
    });
  });

  it("removes the first and the last column without stranding an outer pipe", () => {
    const first = applyEdits(
      BUG_DOCUMENT,
      deleteColumnEdits(BUG_DOCUMENT, tableOf(BUG_DOCUMENT), 0),
    );
    expect(first).toContain(
      "| Status | Priority |\n| --- | --- |\n| Cool | High |",
    );

    const last = applyEdits(
      BUG_DOCUMENT,
      deleteColumnEdits(BUG_DOCUMENT, tableOf(BUG_DOCUMENT), 2),
    );
    expect(last).toContain("| Issue | Status |\n| --- | --- |\n");
    expect(last).toContain(
      "| [PAR-9 – Rent roll import errors](https://linear.app/parth/issue/PAR-9) | Cool |\n",
    );
  });

  it("keeps the alignment markers of the columns that survive", () => {
    const next = applyEdits(
      ALIGNED_TABLE,
      deleteColumnEdits(ALIGNED_TABLE, tableOf(ALIGNED_TABLE), 1),
    );
    expect(next).toContain("| :--- | ---: |");
    expect(tableShapes(next)[0].alignments).toEqual(["left", "right"]);
  });

  it("skips a row that is shorter than the column being deleted", () => {
    const next = applyEdits(
      RAGGED_TABLE,
      deleteColumnEdits(RAGGED_TABLE, tableOf(RAGGED_TABLE), 2),
    );
    expect(next).toBe(
      [
        "| A | B |",
        "| --- | --- |",
        "| 1 | 2 |",
        "| 4 | 5 |",
        "| 6 | 7 | 9 |",
        "",
      ].join("\n"),
    );
    expect(tableShapes(next)[0].rows).toEqual([2, 2, 3]);
  });

  it("leaves a one-cell row alone, having no adjacent pipe to take", () => {
    // Deleting its only cell would leave a blank line — which ends the table and
    // splits it in two — or a bare `|`, which is a row of no cells. The value
    // stays under a heading that moved, which is visible; erasing it would not
    // be.
    const source = "| A | B |\n| --- | --- |\n| 1 | 2 |\n| solo |\n";
    const next = applyEdits(
      source,
      deleteColumnEdits(source, tableOf(source), 0),
    );
    expect(next).toBe("| B |\n| --- |\n| 2 |\n| solo |\n");
    expect(blockKinds(next)).toEqual(["table"]);
    expect(tableShapes(next)[0].rows).toEqual([1, 1]);
  });

  it("puts a pipe back where the deletion takes the character a row starts with", () => {
    // Outer pipes are optional, so `slots[0].start` on a row written without a
    // leading one IS the first character of the line — and deleting column 0
    // there hands the head of the line to whatever cell 1 held. `---` is a
    // thematic break: without the repair this document loses four of its six
    // rows, gains a horizontal rule nobody asked for, and the rows below it
    // become literal pipe-noise in a paragraph. Every assertion is on the far
    // side of a reparse for that reason; the string alone looks plausible.
    const source = [
      "| Item | Note |",
      "| --- | --- |",
      "| a | 1 |",
      "spare | ---",
      "| b | 2 |",
      "| c | 3 |",
      "| d | 4 |",
      "",
    ].join("\n");
    const next = applyEdits(
      source,
      deleteColumnEdits(source, tableOf(source), 0),
    );
    expect(blockKinds(next)).toEqual(["table"]);
    expect(tableShapes(next)[0]).toMatchObject({
      headers: ["Note"],
      alignments: [null],
      rows: [1, 1, 1, 1, 1],
    });
    expect(tableShapes(next)[0].cells).toEqual([
      ["1"],
      ["---"],
      ["2"],
      ["3"],
      ["4"],
    ]);
    // The pipe went in where the line's own opening was, and nothing else moved.
    expect(next).toContain("| 1 |\n| ---\n| 2 |");
  });

  it("puts one back for a lone `-` placeholder, the commonest cell there is", () => {
    // A dash meaning "no value" is ordinary table data. Deleting the column
    // beside it left ` -`, which is a LIST — one click, no notice, and the rows
    // under it stop being rows.
    const source = [
      "| Service | Owner |",
      "| --- | --- |",
      "| api | alice |",
      "api-gateway | -",
      "| web | bob |",
      "",
    ].join("\n");
    const next = applyEdits(
      source,
      deleteColumnEdits(source, tableOf(source), 0),
    );
    expect(blockKinds(next)).toEqual(["table"]);
    expect(tableShapes(next)[0]).toMatchObject({
      headers: ["Owner"],
      rows: [1, 1, 1],
    });
    expect(tableShapes(next)[0].cells).toEqual([["alice"], ["-"], ["bob"]]);
  });

  it("puts one back for a DELIMITER row left holding a pipe and reading as a list", () => {
    // A delimiter cell may be a single `-`, so a delimiter row can be
    // `:-: | - | ---`. Cutting its first column leaves ` - | ---`, which still
    // has a pipe in it and is a LIST — "the delimiter kept a pipe" is not enough
    // on its own to keep the table.
    //
    // This is the one case in this file whose assertion is on the LINE rather
    // than on the reparse, and the reason is worth stating: `parseBlocks` is
    // more permissive here than the renderer the document is shown through.
    // `readDelimiterRow` accepts ` - | ---` as a delimiter row, while remark-gfm
    // reads a list item and turns the header above it into a paragraph — so the
    // block model reports a healthy table over a document that renders as
    // prose, and only the line itself says which one happened. Every row of a
    // table must be a line that cannot start a block, and after a cut that takes
    // a line's opening, the pipe is what guarantees it.
    const source = ["A | B | C", ":-: | - | ---", "1 | 2 | 3", ""].join("\n");
    expect(blockKinds(source)).toEqual(["table"]);
    const next = applyEdits(
      source,
      deleteColumnEdits(source, tableOf(source), 0),
    );
    expect(next).toBe([" B | C", "| - | ---", " 2 | 3", ""].join("\n"));
    expect(blockKinds(next)).toEqual(["table"]);
    expect(tableShapes(next)[0]).toMatchObject({
      headers: ["B", "C"],
      alignments: [null, null],
      rows: [2],
    });
    expect(tableShapes(next)[0].cells).toEqual([["2", "3"]]);
  });

  it("puts one back for a row that keeps its opening but loses its only pipe", () => {
    // The other way a row stops being a row, and it is not column 0: `--- | x`
    // is a row while the pipe is there and a THEMATIC BREAK the moment the last
    // column takes it. The repair is the same one, at the same place.
    const source = [
      "| A | B |",
      "| --- | --- |",
      "--- | x",
      "| c | d |",
      "",
    ].join("\n");
    const next = applyEdits(
      source,
      deleteColumnEdits(source, tableOf(source), 1),
    );
    expect(blockKinds(next)).toEqual(["table"]);
    expect(tableShapes(next)[0]).toMatchObject({
      headers: ["A"],
      rows: [1, 1],
    });
    expect(tableShapes(next)[0].cells).toEqual([["---"], ["c"]]);
  });

  it("keeps the row a row whatever the cell inheriting the line's head holds", () => {
    // The three cases above are examples; this is the class. Each entry is a
    // shape that MEANS something at the start of a line — the block starts the
    // scanner recognizes, which is exactly the set that can end a table — sitting
    // in the cell that is about to become the start of the line.
    const survivors = [
      "# x",
      "> x",
      "```",
      "<div>",
      "1. x",
      "***",
      "---",
      "-",
      "    x",
      "\tx",
    ];
    for (const survivor of survivors) {
      const source = `| A | B |\n| --- | --- |\na | ${survivor}\n`;
      const next = applyEdits(
        source,
        deleteColumnEdits(source, tableOf(source), 0),
      );
      expect(blockKinds(next), survivor).toEqual(["table"]);
      expect(tableShapes(next)[0], survivor).toMatchObject({
        headers: ["B"],
        rows: [1],
      });
      // The cell still holds what the user wrote, and only the opening moved.
      expect(tableShapes(next)[0].cells, survivor).toEqual([[survivor.trim()]]);
    }
  });

  it("adds no pipe to a row that already opens with one", () => {
    // The repair must never land on a row that has a leading pipe: a second one
    // opens an empty first cell and the row's cell count changes under it. Every
    // row of this table has one, so the batch is deletions and nothing else.
    const edits = deleteColumnEdits(BUG_DOCUMENT, tableOf(BUG_DOCUMENT), 0);
    expect(edits.every((edit) => edit.text === "")).toBe(true);
    expect(edits.every((edit) => edit.end > edit.start)).toBe(true);
  });

  it("keeps a pipe in the delimiter row of a table written without outer pipes", () => {
    // `b` over `---` is a SETEXT HEADING, not a one-column table: the deletion
    // would take the delimiter row's only pipe and the whole table would quietly
    // become prose.
    const next = applyEdits(
      HEADERLESS_PIPES,
      deleteColumnEdits(HEADERLESS_PIPES, tableOf(HEADERLESS_PIPES), 0),
    );
    expect(blockKinds(next)).toEqual(["table"]);
    expect(tableShapes(next)[0]).toMatchObject({
      headers: ["b"],
      alignments: [null],
      rows: [1],
    });

    const other = applyEdits(
      HEADERLESS_PIPES,
      deleteColumnEdits(HEADERLESS_PIPES, tableOf(HEADERLESS_PIPES), 1),
    );
    expect(blockKinds(other)).toEqual(["table"]);
    expect(tableShapes(other)[0].headers).toEqual(["a"]);
  });

  it("refuses the last column, and a column the table does not have", () => {
    const source = "| Only |\n| :-: |\n| one |\n";
    expect(() => deleteColumnEdits(source, tableOf(source), 0)).toThrow(
      /last column/,
    );
    expect(() =>
      deleteColumnEdits(BUG_DOCUMENT, tableOf(BUG_DOCUMENT), 3),
    ).toThrow(RangeError);
    expect(() =>
      deleteColumnEdits(BUG_DOCUMENT, tableOf(BUG_DOCUMENT), -1),
    ).toThrow(RangeError);
  });
});

// --- blocks -----------------------------------------------------------------

describe("addBlockEdits", () => {
  it("inserts before a block with the separation the document uses", () => {
    const blocks = parseBlocks(BUG_DOCUMENT);
    const next = applyEdits(
      BUG_DOCUMENT,
      addBlockEdits(BUG_DOCUMENT, blocks, 1, "Intro prose."),
    );
    expect(next).toBe(
      BUG_DOCUMENT.replace("| Issue |", "Intro prose.\n\n| Issue |"),
    );
    expect(blockKinds(next)).toEqual([
      "heading",
      "paragraph",
      "table",
      "paragraph",
    ]);
  });

  it("appends after the last block's content, not after the document's padding", () => {
    const blocks = parseBlocks(BUG_DOCUMENT);
    const next = applyEdits(
      BUG_DOCUMENT,
      addBlockEdits(BUG_DOCUMENT, blocks, blocks.length, INSERTED),
    );
    expect(next).toBe(`${BUG_DOCUMENT.trimEnd()}\n\n${INSERTED}\n`);
    expect(blockKinds(next)).toEqual([
      "heading",
      "table",
      "paragraph",
      "heading",
    ]);
  });

  it("adds a blank line above when the block it follows does not end in one", () => {
    // `> quoted` runs on into the line below it, so a paragraph written straight
    // under it is not a block — it is the quote's lazy continuation.
    const source = "> quoted line\n# Heading\n\nBody.\n";
    const blocks = parseBlocks(source);
    const next = applyEdits(
      source,
      addBlockEdits(source, blocks, 1, "Middle."),
    );
    expect(next).toBe("> quoted line\n\n\nMiddle.\n\n# Heading\n\nBody.\n");
    expect(blockKinds(next)).toEqual([
      "raw:blockquote",
      "paragraph",
      "heading",
      "paragraph",
    ]);
  });

  it("starts a document that has no blocks at all", () => {
    expect(applyEdits("", addBlockEdits("", [], 0, INSERTED))).toBe(INSERTED);
    expect(
      blockKinds(applyEdits("", addBlockEdits("", [], 0, INSERTED))),
    ).toEqual(["heading"]);
  });

  it("keeps CRLF, and keeps a missing final newline missing", () => {
    const crlfBlocks = parseBlocks(CRLF_DOCUMENT);
    const crlf = applyEdits(
      CRLF_DOCUMENT,
      addBlockEdits(CRLF_DOCUMENT, crlfBlocks, crlfBlocks.length, INSERTED),
    );
    expect(crlf).toBe(`${CRLF_DOCUMENT.trimEnd()}\r\n\r\n${INSERTED}\r\n`);
    expect(crlf.replace(/\r\n/g, "")).not.toMatch(/[\r\n]/);

    const bareBlocks = parseBlocks(NO_TRAILING_NEWLINE);
    const bare = applyEdits(
      NO_TRAILING_NEWLINE,
      addBlockEdits(
        NO_TRAILING_NEWLINE,
        bareBlocks,
        bareBlocks.length,
        INSERTED,
      ),
    );
    expect(bare).toBe(`${NO_TRAILING_NEWLINE}\n\n${INSERTED}`);
    expect(bare.endsWith("\n")).toBe(false);
  });

  it("writes a clean blank line into a document whose blank lines carry spaces", () => {
    // A blank line holding a space separates two blocks exactly as an empty one
    // does, and nobody can see the difference. Copying that run into a boundary
    // the user just asked for would spread invisible whitespace to a place it
    // was never typed — so the SHAPE is measured (how many lines, in whose
    // terminator) and the bytes that filled it are not carried along.
    const source = ["# One", " ", "Two.", " ", "Three.", ""].join("\n");
    const blocks = parseBlocks(source);
    const next = applyEdits(source, addBlockEdits(source, blocks, 1, INSERTED));

    expect(next).toBe("# One\n \n## Inserted\n\nTwo.\n \nThree.\n");
    expect(blockKinds(next)).toEqual([
      "heading",
      "heading",
      "paragraph",
      "paragraph",
    ]);
    // The runs the document already had are untouched: normalizing what is
    // INSERTED is not licence to reformat what is there.
    expect(next).toContain("# One\n \n");
    expect(next).toContain("Two.\n \nThree.");

    // Same rule in a CRLF document: the terminator is the document's, the
    // padding is nobody's.
    const crlfSource = "# One\r\n \r\nTwo.\r\n";
    const crlfNext = applyEdits(
      crlfSource,
      addBlockEdits(crlfSource, parseBlocks(crlfSource), 1, INSERTED),
    );
    expect(crlfNext).toBe("# One\r\n \r\n## Inserted\r\n\r\nTwo.\r\n");
    expect(crlfNext.replace(/\r\n/g, "")).not.toMatch(/[\r\n]/);
  });

  it("counts a spaced blank line and a bare one as the same separation", () => {
    // Measured in the form it would be written in, so a document that uses both
    // has one style rather than two — and the tie-break never elects the run
    // with the invisible padding in it.
    const source = ["# One", " ", "Two.", "", "Three.", ""].join("\n");
    const blocks = parseBlocks(source);
    const next = applyEdits(
      source,
      addBlockEdits(source, blocks, blocks.length, INSERTED),
    );
    expect(next).toBe("# One\n \nTwo.\n\nThree.\n\n## Inserted\n");
    expect(blockKinds(next)).toEqual([
      "heading",
      "paragraph",
      "paragraph",
      "heading",
    ]);
  });

  it("lands inside an unclosed fence, because an unclosed fence runs to the end", () => {
    // Not a defect to fix here: the fence really does own the rest of the
    // document. The operation guarantees a blank line, not that markdown will
    // agree to stop a construct that never closed. Pinned rather than skipped,
    // so a change in the behaviour shows up as a failing test instead of as a
    // corpus entry nobody exercises.
    const source = "Intro\n\n```js\nconst a = 1;\n";
    const blocks = parseBlocks(source);
    const next = applyEdits(
      source,
      addBlockEdits(source, blocks, blocks.length, INSERTED),
    );
    expect(blockKinds(next)).toEqual(["paragraph", "raw:fenced-code"]);
    expect(next).toBe(`Intro\n\n\`\`\`js\nconst a = 1;\n\n${INSERTED}\n`);

    // Same fact reached from the other side: move the fence to the top and the
    // paragraph that follows it is code.
    const swapped = applyEdits(source, swapBlocksEdits(source, blocks, 0, 1));
    expect(swapped).toBe("```js\nconst a = 1;\n\nIntro\n");
    expect(blockKinds(swapped)).toEqual(["raw:fenced-code"]);
  });

  it("refuses blank text and a boundary that does not exist", () => {
    const blocks = parseBlocks(BUG_DOCUMENT);
    expect(() => addBlockEdits(BUG_DOCUMENT, blocks, 1, "  \n ")).toThrow(
      /needs text/,
    );
    expect(() => addBlockEdits(BUG_DOCUMENT, blocks, 9, INSERTED)).toThrow(
      RangeError,
    );
    // An empty HEADING is a real block, so this one is allowed.
    expect(
      applyEdits(BUG_DOCUMENT, addBlockEdits(BUG_DOCUMENT, blocks, 0, "## ")),
    ).toContain("## \n\n# My Assigned");
  });
});

describe("deleteBlockEdits", () => {
  it("takes the footprint of a middle block, blank run and all", () => {
    const blocks = parseBlocks(BUG_DOCUMENT);
    const next = applyEdits(
      BUG_DOCUMENT,
      deleteBlockEdits(BUG_DOCUMENT, blocks, 1),
    );
    expect(next).toBe(
      "# My Assigned Linear Issues\n\nAll five issues are currently in **Cool** status.\n",
    );
    expect(blockKinds(next)).toEqual(["heading", "paragraph"]);
  });

  it("keeps a blank line between the two blocks a deletion introduces", () => {
    // Without it `Para one` and `Para two` become ONE paragraph — the deletion
    // would silently merge two blocks it was not asked about.
    const source = "Para one\n# H\n\nPara two\n";
    const blocks = parseBlocks(source);
    const next = applyEdits(source, deleteBlockEdits(source, blocks, 1));
    expect(next).toBe("Para one\n\nPara two\n");
    expect(blockKinds(next)).toEqual(["paragraph", "paragraph"]);
  });

  it("takes the blank run above the last block and restores the final newline", () => {
    const blocks = parseBlocks(BUG_DOCUMENT);
    const next = applyEdits(
      BUG_DOCUMENT,
      deleteBlockEdits(BUG_DOCUMENT, blocks, 2),
    );
    expect(next).toBe(
      BUG_DOCUMENT.replace(
        "\n\nAll five issues are currently in **Cool** status.\n",
        "\n",
      ),
    );
    expect(next.endsWith("| Cool | Low |\n")).toBe(true);
  });

  it("does not give a document without a final newline one", () => {
    const blocks = parseBlocks(NO_TRAILING_NEWLINE);
    const next = applyEdits(
      NO_TRAILING_NEWLINE,
      deleteBlockEdits(NO_TRAILING_NEWLINE, blocks, 1),
    );
    expect(next).toBe("# Title");
    expect(blockKinds(next)).toEqual(["heading"]);
  });

  it("empties the document when the only block goes", () => {
    const blocks = parseBlocks(HEADERLESS_PIPES);
    expect(
      applyEdits(
        HEADERLESS_PIPES,
        deleteBlockEdits(HEADERLESS_PIPES, blocks, 0),
      ),
    ).toBe("");
    expect(parseBlocks("")).toEqual([]);
  });

  it("refuses a block the document does not have", () => {
    const blocks = parseBlocks(BUG_DOCUMENT);
    expect(() => deleteBlockEdits(BUG_DOCUMENT, blocks, 3)).toThrow(RangeError);
    expect(() => deleteBlockEdits(BUG_DOCUMENT, blocks, -1)).toThrow(
      RangeError,
    );
  });
});

describe("swapBlocksEdits", () => {
  it("exchanges the content and leaves each slot's separation where it was", () => {
    const blocks = parseBlocks(BUG_DOCUMENT);
    const next = applyEdits(
      BUG_DOCUMENT,
      swapBlocksEdits(BUG_DOCUMENT, blocks, 0, 2),
    );
    expect(next).toBe(
      [
        "All five issues are currently in **Cool** status.",
        "",
        "| Issue | Status | Priority |",
        "| --- | --- | --- |",
        "| [PAR-9 – Rent roll import errors](https://linear.app/parth/issue/PAR-9) | Cool | High |",
        "| [PAR-12 – Sync retry backoff](https://linear.app/parth/issue/PAR-12) | Cool | Medium |",
        "| [PAR-14 – Webhook replay gap](https://linear.app/parth/issue/PAR-14) | Cool | Low |",
        // The table's own trailing blank line, which stayed in the slot it
        // belongs to: a reorder moves content, never the spacing around it.
        "",
        "# My Assigned Linear Issues",
        "",
      ].join("\n"),
    );
    expect(blockKinds(next)).toEqual(["paragraph", "table", "heading"]);
  });

  it("separates a block moved into a slot that only had a newline", () => {
    // Moving `More.` under `Text` with the slot's own single newline welds the
    // two into one paragraph. The moved block gets the blank line it needs.
    const source = "# H\nText\n\nMore.\n";
    const blocks = parseBlocks(source);
    const next = applyEdits(source, swapBlocksEdits(source, blocks, 0, 2));
    expect(next).toBe("More.\n\nText\n\n# H\n");
    expect(blockKinds(next)).toEqual(["paragraph", "paragraph", "heading"]);
  });

  it("moves a block up by swapping it with its neighbour", () => {
    const source = "# A\n\nBody.\n\n## B\n\nMore body.\n";
    const blocks = parseBlocks(source);
    const next = applyEdits(source, swapBlocksEdits(source, blocks, 2, 1));
    expect(next).toBe("# A\n\n## B\n\nBody.\n\nMore body.\n");
    expect(blockKinds(next)).toEqual([
      "heading",
      "heading",
      "paragraph",
      "paragraph",
    ]);
  });

  it("keeps the final newline where the last slot has it", () => {
    const blocks = parseBlocks(NO_TRAILING_NEWLINE);
    const next = applyEdits(
      NO_TRAILING_NEWLINE,
      swapBlocksEdits(NO_TRAILING_NEWLINE, blocks, 0, 1),
    );
    expect(next).toBe("| A | B |\n| --- | --- |\n| 1 | 2 |\n\n# Title");
    expect(next.endsWith("\n")).toBe(false);
    expect(blockKinds(next)).toEqual(["table", "heading"]);
  });

  it("has nothing to hang a missing final newline on when the last content moves away", () => {
    // A document's leading run of blank lines is a BLOCK. Swap the last block
    // into it and the last slot is empty, so the document now ends where the
    // block above it ends — with that block's terminator. Every byte of content
    // is still there, in the order asked for; what moved is trailing whitespace
    // the last block was the only thing carrying.
    const source = "\n\n# Title\n\nTail.";
    const blocks = parseBlocks(source);
    const next = applyEdits(source, swapBlocksEdits(source, blocks, 0, 2));
    expect(next).toBe("Tail.\n\n# Title\n\n");
    expect(blockKinds(next)).toEqual(["paragraph", "heading"]);
  });

  it("is the identity for a block swapped with itself", () => {
    const blocks = parseBlocks(BUG_DOCUMENT);
    expect(swapBlocksEdits(BUG_DOCUMENT, blocks, 1, 1)).toEqual([]);
    expect(
      applyEdits(BUG_DOCUMENT, swapBlocksEdits(BUG_DOCUMENT, blocks, 1, 1)),
    ).toBe(BUG_DOCUMENT);
  });

  it("refuses a block the document does not have", () => {
    const blocks = parseBlocks(BUG_DOCUMENT);
    expect(() => swapBlocksEdits(BUG_DOCUMENT, blocks, 0, 3)).toThrow(
      RangeError,
    );
    expect(() => swapBlocksEdits(BUG_DOCUMENT, blocks, -1, 0)).toThrow(
      RangeError,
    );
    // An empty document has no block index at all, which is not the same as
    // having a boundary at 0 — `addBlockEdits` accepts that one.
    expect(() => swapBlocksEdits("", [], 0, 0)).toThrow(RangeError);
    expect(() => deleteBlockEdits("", [], 0)).toThrow(RangeError);
  });
});

// --- composition ------------------------------------------------------------

describe("structural edits compose with the rest of a batch", () => {
  // This is what returning `DocumentEdit[]` is FOR. A second application path —
  // "apply the structural change, then re-parse, then apply the text changes" —
  // would need the text edits' offsets re-derived against a string that no
  // longer exists, which is the class of bug the whole model is built to avoid.
  it("applies a new row, a cell edit and a prose edit in one call", () => {
    const table = tableOf(BUG_DOCUMENT);
    const paragraph = parseBlocks(BUG_DOCUMENT)[2];
    if (paragraph.kind !== "paragraph") throw new Error("expected prose");

    const next = applyEdits(BUG_DOCUMENT, [
      ...addRowEdits(BUG_DOCUMENT, table),
      cellEdit(table, 0, 1, "Warm"),
      blockEdit(paragraph, "Four issues now."),
    ]);

    expect(blockKinds(next)).toEqual(["heading", "table", "paragraph"]);
    expect(tableShapes(next)[0].rows).toEqual([3, 3, 3, 3]);
    expect(next).toContain("| Warm | High |");
    expect(next.endsWith("Four issues now.\n")).toBe(true);
  });

  it("applies two structural edits that touch different lines", () => {
    const table = tableOf(BUG_DOCUMENT);
    const next = applyEdits(BUG_DOCUMENT, [
      ...deleteRowEdits(BUG_DOCUMENT, table, 0),
      ...addRowEdits(BUG_DOCUMENT, table),
    ]);
    expect(tableShapes(next)[0].cells).toEqual([
      [
        "[PAR-12 – Sync retry backoff](https://linear.app/parth/issue/PAR-12)",
        "Cool",
        "Medium",
      ],
      [
        "[PAR-14 – Webhook replay gap](https://linear.app/parth/issue/PAR-14)",
        "Cool",
        "Low",
      ],
      ["", "", ""],
    ]);
  });

  it("throws on a batch that edits a row it also deletes", () => {
    // Adding a column writes into EVERY row, including the one being deleted.
    // `applyEdits` refuses the overlap rather than picking a winner, which is
    // the only safe answer: either resolution loses an edit the caller made.
    const table = tableOf(BUG_DOCUMENT);
    expect(() =>
      applyEdits(BUG_DOCUMENT, [
        ...addColumnEdits(BUG_DOCUMENT, table),
        ...deleteRowEdits(BUG_DOCUMENT, table, 0),
      ]),
    ).toThrow(/overlap/);
  });

  it("never generates an overlapping pair on its own", () => {
    // Every corpus property already proves this by applying each batch — this
    // states it directly for the batches with the most edits in them.
    for (const source of Object.values(MARKDOWN_CORPUS)) {
      for (const table of tablesOf(source)) {
        expect(() =>
          applyEdits(source, addColumnEdits(source, table)),
        ).not.toThrow();
        if (table.headers.length < 2) continue;
        for (let column = 0; column < table.headers.length; column += 1) {
          expect(() =>
            applyEdits(source, deleteColumnEdits(source, table, column)),
          ).not.toThrow();
        }
      }
    }
  });
});

// --- stale input ------------------------------------------------------------

describe("blocks parsed from a different string", () => {
  // The failure this prevents is the worst one available: offsets that mean
  // nothing splice into the middle of a sentence, and nothing about the result
  // says an edit went wrong.
  const stale = parseBlocks(BUG_DOCUMENT);
  const staleTable = tableOf(BUG_DOCUMENT);
  const other = "# Something else entirely\n\nWith different prose.\n";

  it("is refused by every table operation", () => {
    expect(() => addRowEdits(other, staleTable)).toThrow(RangeError);
    expect(() => deleteRowEdits(other, staleTable, 0)).toThrow(RangeError);
    expect(() => addColumnEdits(other, staleTable)).toThrow(RangeError);
    expect(() => deleteColumnEdits(other, staleTable, 0)).toThrow(RangeError);
  });

  it("is refused by every block operation", () => {
    expect(() => addBlockEdits(other, stale, 1, INSERTED)).toThrow(RangeError);
    expect(() => deleteBlockEdits(other, stale, 1)).toThrow(RangeError);
    expect(() => swapBlocksEdits(other, stale, 0, 1)).toThrow(RangeError);
  });
});

// --- the corpus -------------------------------------------------------------

describe("every table operation over the corpus", () => {
  for (const [name, source] of Object.entries(MARKDOWN_CORPUS)) {
    if (tablesOf(source).length === 0) continue;

    it(`adds a row to every table in ${name}`, () => {
      const before = tableShapes(source);
      tablesOf(source).forEach((table, index) => {
        const next = applyEdits(source, addRowEdits(source, table));
        assertDocumentIntact(source, next, `${name} table ${index}`);
        expect(blockKinds(next)).toEqual(blockKinds(source));
        const after = tableShapes(next)[index];
        expect(after.headers).toEqual(before[index].headers);
        expect(after.alignments).toEqual(before[index].alignments);
        expect(after.rows).toEqual([
          ...before[index].rows,
          before[index].headers.length,
        ]);
      });
    });

    it(`deletes every row of every table in ${name}`, () => {
      const before = tableShapes(source);
      tablesOf(source).forEach((table, index) => {
        before[index].rows.forEach((_, row) => {
          const next = applyEdits(source, deleteRowEdits(source, table, row));
          assertDocumentIntact(
            source,
            next,
            `${name} table ${index} row ${row}`,
          );
          expect(blockKinds(next)).toEqual(blockKinds(source));
          const after = tableShapes(next)[index];
          expect(after.headers).toEqual(before[index].headers);
          expect(after.cells).toEqual(
            before[index].cells.filter((_cells, at) => at !== row),
          );
        });
      });
    });

    it(`adds a column to every table in ${name}`, () => {
      const before = tableShapes(source);
      tablesOf(source).forEach((table, index) => {
        const next = applyEdits(source, addColumnEdits(source, table));
        assertDocumentIntact(source, next, `${name} table ${index}`);
        expect(blockKinds(next)).toEqual(blockKinds(source));
        const after = tableShapes(next)[index];
        expect(after.headers).toEqual([...before[index].headers, ""]);
        // The delimiter row kept up with the header — which is the difference
        // between a table and two paragraphs.
        expect(after.alignments).toHaveLength(after.headers.length);
        expect(after.alignments).toEqual([
          ...before[index].alignments,
          before[index].alignments[before[index].alignments.length - 1],
        ]);
        expect(after.rows).toEqual(
          before[index].rows.map((count) => (count === 0 ? 0 : count + 1)),
        );
      });
    });

    it(`deletes every column of every table in ${name}`, () => {
      const before = tableShapes(source);
      tablesOf(source).forEach((table, index) => {
        const columns = before[index].headers.length;
        if (columns < 2) {
          expect(() => deleteColumnEdits(source, table, 0)).toThrow(RangeError);
          return;
        }
        for (let column = 0; column < columns; column += 1) {
          const next = applyEdits(
            source,
            deleteColumnEdits(source, table, column),
          );
          assertDocumentIntact(
            source,
            next,
            `${name} table ${index} column ${column}`,
          );
          expect(blockKinds(next)).toEqual(blockKinds(source));
          const after = tableShapes(next)[index];
          expect(after.headers).toEqual(
            before[index].headers.filter((_header, at) => at !== column),
          );
          expect(after.alignments).toEqual(
            before[index].alignments.filter((_alignment, at) => at !== column),
          );
          expect(after.alignments).toHaveLength(after.headers.length);
          expect(after.rows).toEqual(
            before[index].rows.map((count) =>
              count > column && count >= 2 ? count - 1 : count,
            ),
          );
        }
      });
    });
  }
});

describe("every block operation over the corpus", () => {
  for (const [name, source] of Object.entries(MARKDOWN_CORPUS)) {
    it(`inserts a block at every boundary of ${name}`, () => {
      const blocks = parseBlocks(source);
      const before = signatures(source);
      for (let index = 0; index <= blocks.length; index += 1) {
        // Appending after an unclosed fence puts the text inside it, which the
        // dedicated test above pins as the behaviour it is.
        if (name === "UNCLOSED_FENCE" && index === blocks.length) continue;
        const label = `${name} boundary ${index}`;
        const next = applyEdits(
          source,
          addBlockEdits(source, blocks, index, INSERTED),
        );
        assertDocumentIntact(source, next, label);
        expect(contentful(signatures(next)), label).toEqual(
          contentful([
            ...before.slice(0, index),
            { kind: "heading", text: INSERTED },
            ...before.slice(index),
          ]),
        );
      }
    });

    it(`deletes every block of ${name}`, () => {
      const blocks = parseBlocks(source);
      const before = signatures(source);
      for (let index = 0; index < blocks.length; index += 1) {
        const label = `${name} block ${index}`;
        const next = applyEdits(
          source,
          deleteBlockEdits(source, blocks, index),
        );
        assertDocumentIntact(source, next, label);
        expect(contentful(signatures(next)), label).toEqual(
          contentful(before.filter((_entry, at) => at !== index)),
        );
      }
    });

    it(`swaps every pair of blocks in ${name}`, () => {
      const blocks = parseBlocks(source);
      const before = signatures(source);
      // Moving an unclosed fence above another block makes that block code —
      // see the dedicated test, which asserts exactly what happens instead.
      if (name === "UNCLOSED_FENCE") return;
      for (let first = 0; first < blocks.length; first += 1) {
        for (let second = first + 1; second < blocks.length; second += 1) {
          const label = `${name} blocks ${first} and ${second}`;
          const next = applyEdits(
            source,
            swapBlocksEdits(source, blocks, first, second),
          );
          assertDocumentIntact(source, next, label);
          const expected = [...before];
          expected[first] = before[second];
          expected[second] = before[first];
          expect(contentful(signatures(next)), label).toEqual(
            contentful(expected),
          );
        }
      }
    });
  }
});

/**
 * The same coverage property the scanner is held to, re-run over the documents
 * these operations PRODUCE.
 *
 * The generator's separator list includes a bare `"\n"`, so it builds the
 * arrangement every block operation has to survive: two constructs with no blank
 * line between them, where a paragraph swallows the block above it and a
 * container continues into the one below.
 */
describe("500 generated documents survive every operation", () => {
  /**
   * What a TABLE operation holds that a block operation cannot: the document's
   * block kinds are unchanged, because everything that moved was inside a table
   * that is still a table.
   *
   * `assertDocumentIntact` proves the model still covers every byte, and a
   * document where the table quietly became a thematic break and three
   * paragraphs passes it comfortably — the bytes are all still there, in blocks
   * that all still tile. Comparing the KINDS is what says the operation did what
   * it was asked and nothing else.
   */
  function assertTableOp(source: string, next: string, label: string): void {
    assertDocumentIntact(source, next, label);
    expect(blockKinds(next), label).toEqual(blockKinds(source));
  }

  function eachOperation(source: string, iteration: number): void {
    const blocks = parseBlocks(source);
    for (let index = 0; index <= blocks.length; index += 1) {
      assertDocumentIntact(
        source,
        applyEdits(source, addBlockEdits(source, blocks, index, INSERTED)),
        `iteration ${iteration} add block at ${index} of ${JSON.stringify(source)}`,
      );
    }
    for (let index = 0; index < blocks.length; index += 1) {
      assertDocumentIntact(
        source,
        applyEdits(source, deleteBlockEdits(source, blocks, index)),
        `iteration ${iteration} delete block ${index} of ${JSON.stringify(source)}`,
      );
      const partner = (index + 1) % blocks.length;
      // Swapping with a CONTENT-LESS block moves the document's last content
      // away, and "ends without a newline" then has nothing to live on. Pinned
      // by its own test above; the blanket invariant does not cover it.
      if (
        hasContent(source, blocks[index]) &&
        hasContent(source, blocks[partner])
      ) {
        assertDocumentIntact(
          source,
          applyEdits(source, swapBlocksEdits(source, blocks, index, partner)),
          `iteration ${iteration} swap block ${index} of ${JSON.stringify(source)}`,
        );
      }
    }
    for (const table of tablesOf(source)) {
      const label = `iteration ${iteration} table of ${JSON.stringify(source)}`;
      assertTableOp(
        source,
        applyEdits(source, addRowEdits(source, table)),
        `${label} add row`,
      );
      assertTableOp(
        source,
        applyEdits(source, addColumnEdits(source, table)),
        `${label} add column`,
      );
      table.rows.forEach((_row, at) => {
        assertTableOp(
          source,
          applyEdits(source, deleteRowEdits(source, table, at)),
          `${label} delete row ${at}`,
        );
      });
      for (let column = 1; column < table.headers.length; column += 1) {
        assertTableOp(
          source,
          applyEdits(source, deleteColumnEdits(source, table, column)),
          `${label} delete column ${column}`,
        );
        assertTableOp(
          source,
          applyEdits(source, deleteColumnEdits(source, table, column - 1)),
          `${label} delete column ${column - 1}`,
        );
      }
    }
  }

  it("keeps the block model covering every byte of the result", () => {
    const random = mulberry32(0x5eed);
    for (let iteration = 0; iteration < 500; iteration += 1) {
      eachOperation(generateDocument(random), iteration);
    }
  });
});
