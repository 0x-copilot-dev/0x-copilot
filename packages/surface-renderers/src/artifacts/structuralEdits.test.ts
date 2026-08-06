// The staging rules, tested where they are decided rather than through a click.
//
// Two things live here that the component test cannot state as sharply:
//
//   * `insert-row` is the one operation assembled from two builders rather than
//     taken whole from one, so its four placements — mid-table, after the last
//     row, in a document that stops without a newline, in a CRLF document — are
//     each written out. A row inserted with the terminator on the wrong side is
//     a blank line inside a table, which ENDS the table and turns the rest of it
//     into prose.
//   * the remap is verified, not trusted. The test that matters is the one where
//     the index arithmetic is WRONG and the change is refused: nothing else
//     stops a pending edit being applied to a span the user never typed into.

import { parseBlocks, type DocumentBlock } from "@0x-copilot/chat-surface";
import { describe, expect, it } from "vitest";

import type { EditTarget, PendingEdits } from "./documentEdits";
import {
  remapTarget,
  stageStructural,
  structuralEditsFor,
  type StructuralOp,
} from "./structuralEdits";

const BOARD = [
  "# Sprint board",
  "",
  "| Issue | Status |",
  "| --- | --- |",
  "| PAR-9 | Cool |",
  "| PAR-12 | Warm |",
  "",
].join("\n");

function staged(source: string, op: StructuralOp, pending: PendingEdits = {}) {
  return stageStructural(source, parseBlocks(source), pending, op);
}

function cell(block: number, row: number, column: number): EditTarget {
  return { kind: "cell", block, row, column };
}

function pendingCell(
  block: number,
  row: number,
  column: number,
  value: string,
): PendingEdits {
  return {
    [`c:${block}:${row}:${column}`]: {
      target: cell(block, row, column),
      value,
    },
  };
}

function kindsOf(source: string): string[] {
  return parseBlocks(source).map((block: DocumentBlock) =>
    block.kind === "raw" ? `raw:${block.reason}` : block.kind,
  );
}

describe("structuralEditsFor — insert a row after a given one", () => {
  it("writes the new row on the line under the row it follows", () => {
    expect(staged(BOARD, { kind: "insert-row", block: 1, row: 0 }).source).toBe(
      [
        "# Sprint board",
        "",
        "| Issue | Status |",
        "| --- | --- |",
        "| PAR-9 | Cool |",
        "|  |  |",
        "| PAR-12 | Warm |",
        "",
      ].join("\n"),
    );
  });

  it("appends after the last row, and the document still ends exactly as it did", () => {
    const source = staged(BOARD, {
      kind: "insert-row",
      block: 1,
      row: 1,
    }).source;
    expect(source).toBe(
      [
        "# Sprint board",
        "",
        "| Issue | Status |",
        "| --- | --- |",
        "| PAR-9 | Cool |",
        "| PAR-12 | Warm |",
        "|  |  |",
        "",
      ].join("\n"),
    );
    expect(kindsOf(source)).toEqual(["heading", "table"]);
  });

  it("keeps a document that ends without a newline ending without one", () => {
    // The last row carries no terminator, so the new row cannot be written
    // AFTER one — the terminator goes in front of it instead.
    const source = ["| A | B |", "| --- | --- |", "| 1 | 2 |"].join("\n");
    expect(
      staged(source, { kind: "insert-row", block: 0, row: 0 }).source,
    ).toBe(["| A | B |", "| --- | --- |", "| 1 | 2 |", "|  |  |"].join("\n"));
    // …and inserting BEFORE that unterminated row is the other side of the same
    // case: the point is a line start, so the terminator goes after the row.
    const two = ["| A | B |", "| --- | --- |", "| 1 | 2 |", "| 3 | 4 |"].join(
      "\n",
    );
    expect(staged(two, { kind: "insert-row", block: 0, row: 0 }).source).toBe(
      ["| A | B |", "| --- | --- |", "| 1 | 2 |", "|  |  |", "| 3 | 4 |"].join(
        "\n",
      ),
    );
  });

  it("inserts a CRLF row into a CRLF document", () => {
    const source = ["| A | B |", "| --- | --- |", "| 1 | 2 |", ""].join("\r\n");
    const next = staged(source, {
      kind: "insert-row",
      block: 0,
      row: 0,
    }).source;
    expect(next).toBe(
      ["| A | B |", "| --- | --- |", "| 1 | 2 |", "|  |  |", ""].join("\r\n"),
    );
    expect(next.includes("\n\n")).toBe(false);
  });

  it("refuses a row index the table does not have", () => {
    expect(() =>
      staged(BOARD, { kind: "insert-row", block: 1, row: 9 }),
    ).toThrow(RangeError);
  });
});

describe("structuralEditsFor — new blocks", () => {
  it("adds a paragraph a user can type over, and a table with a row to type into", () => {
    const paragraph = staged(BOARD, {
      kind: "add-block",
      boundary: 0,
      template: "paragraph",
    }).source;
    expect(paragraph.startsWith("New paragraph\n\n# Sprint board")).toBe(true);
    expect(kindsOf(paragraph)).toEqual(["paragraph", "heading", "table"]);

    const table = staged(BOARD, {
      kind: "add-block",
      boundary: 2,
      template: "table",
    }).source;
    expect(kindsOf(table)).toEqual(["heading", "table", "table"]);
    const added = parseBlocks(table)[2];
    if (added.kind !== "table") throw new Error("expected a table");
    expect(added.headers).toEqual(["Column 1", "Column 2"]);
    expect(added.rows).toHaveLength(1);
  });

  it("writes a new table in the document's own line ending", () => {
    const source = ["Prose.", ""].join("\r\n");
    const next = staged(source, {
      kind: "add-block",
      boundary: 1,
      template: "table",
    }).source;
    expect(next.includes("\n\n")).toBe(false);
    expect(kindsOf(next)).toEqual(["paragraph", "table"]);
  });

  it("starts a document from nothing, and empties one back to nothing", () => {
    expect(
      staged("", { kind: "add-block", boundary: 0, template: "paragraph" })
        .source,
    ).toBe("New paragraph");
    expect(
      staged("Only this.\n", { kind: "delete-block", block: 0 }).source,
    ).toBe("");
  });
});

describe("remapTarget", () => {
  const cases: readonly (readonly [
    string,
    StructuralOp,
    EditTarget,
    EditTarget | null,
  ])[] = [
    [
      "append leaves every row where it is",
      { kind: "add-row", block: 1 },
      cell(1, 0, 0),
      cell(1, 0, 0),
    ],
    [
      "append leaves every column where it is",
      { kind: "add-column", block: 1 },
      cell(1, 0, 1),
      cell(1, 0, 1),
    ],
    [
      "a row under an inserted one moves down",
      { kind: "insert-row", block: 1, row: 0 },
      cell(1, 1, 0),
      cell(1, 2, 0),
    ],
    [
      "a row above an inserted one stays",
      { kind: "insert-row", block: 1, row: 1 },
      cell(1, 0, 0),
      cell(1, 0, 0),
    ],
    [
      "a deleted row's own edit goes with it",
      { kind: "delete-row", block: 1, row: 0 },
      cell(1, 0, 1),
      null,
    ],
    [
      "a row under a deleted one moves up",
      { kind: "delete-row", block: 1, row: 0 },
      cell(1, 2, 1),
      cell(1, 1, 1),
    ],
    [
      "another table's rows are untouched",
      { kind: "delete-row", block: 1, row: 0 },
      cell(3, 0, 0),
      cell(3, 0, 0),
    ],
    [
      "a deleted column's own edit goes with it",
      { kind: "delete-column", block: 1, column: 1 },
      cell(1, 0, 1),
      null,
    ],
    [
      "a column right of a deleted one moves left",
      { kind: "delete-column", block: 1, column: 0 },
      cell(1, 0, 2),
      cell(1, 0, 1),
    ],
    [
      "a header cell moves with its column",
      { kind: "delete-column", block: 1, column: 0 },
      { kind: "header", block: 1, column: 2 },
      { kind: "header", block: 1, column: 1 },
    ],
    [
      "a prose block has no column to move",
      { kind: "delete-column", block: 1, column: 0 },
      { kind: "prose", block: 4 },
      { kind: "prose", block: 4 },
    ],
    [
      "blocks after an inserted one move down",
      { kind: "add-block", boundary: 1, template: "paragraph" },
      { kind: "prose", block: 1 },
      { kind: "prose", block: 2 },
    ],
    [
      "a deleted block's own edit goes with it",
      { kind: "delete-block", block: 2 },
      { kind: "prose", block: 2 },
      null,
    ],
    [
      "blocks after a deleted one move up",
      { kind: "delete-block", block: 0 },
      cell(2, 1, 0),
      cell(1, 1, 0),
    ],
    [
      "a swapped block's edits travel with it",
      { kind: "swap-blocks", first: 1, second: 2 },
      cell(1, 0, 0),
      cell(2, 0, 0),
    ],
    [
      "…in both directions",
      { kind: "swap-blocks", first: 1, second: 2 },
      { kind: "prose", block: 2 },
      { kind: "prose", block: 1 },
    ],
  ];

  for (const [name, op, target, expected] of cases) {
    it(name, () => expect(remapTarget(op, target)).toEqual(expected));
  }
});

describe("stageStructural", () => {
  it("carries a pending cell edit across a row deleted above it", () => {
    const result = staged(
      BOARD,
      { kind: "delete-row", block: 1, row: 0 },
      pendingCell(1, 1, 1, "Hot"),
    );
    expect(result.pending).toEqual(pendingCell(1, 0, 1, "Hot"));
  });

  it("drops the edit typed into the row being deleted, and only that one", () => {
    const result = staged(
      BOARD,
      { kind: "delete-row", block: 1, row: 0 },
      { ...pendingCell(1, 0, 1, "gone"), ...pendingCell(1, 1, 0, "kept") },
    );
    expect(result.pending).toEqual(pendingCell(1, 0, 0, "kept"));
  });

  it("refuses the whole change when a pending edit cannot be re-addressed", () => {
    // A document whose FIRST block is a run of blank lines, and a paragraph
    // inserted above it: the blank run is absorbed into the new paragraph's
    // footprint, so the blocks do not shift by one the way the arithmetic
    // assumes. The check on the far side is what catches it — without it the
    // pending edit would be spliced into a span nobody typed into.
    const source = "\n\n# Title\n\nSome prose.\n";
    const blocks = parseBlocks(source);
    expect(blocks).toHaveLength(3);
    const pending: PendingEdits = {
      "p:1": { target: { kind: "prose", block: 1 }, value: "Retitled" },
    };
    expect(() =>
      stageStructural(source, blocks, pending, {
        kind: "add-block",
        boundary: 0,
        template: "paragraph",
      }),
    ).toThrow(RangeError);
    // With nothing pending there is nothing to misplace, and the same change is
    // simply applied.
    expect(
      stageStructural(
        source,
        blocks,
        {},
        {
          kind: "add-block",
          boundary: 0,
          template: "paragraph",
        },
      ).source,
    ).toBe("New paragraph\n\n\n\n# Title\n\nSome prose.\n");
  });

  it("refuses an operation the block package itself rejects", () => {
    const oneColumn = ["| Only |", "| --- |", "| a |", ""].join("\n");
    expect(() =>
      staged(oneColumn, { kind: "delete-column", block: 0, column: 0 }),
    ).toThrow(RangeError);
    expect(() => staged(BOARD, { kind: "add-row", block: 0 })).toThrow(
      RangeError,
    );
  });

  it("computes its edits against the ORIGINAL source, every time", () => {
    // Every operation returns spans into the string it was handed, so a caller
    // that applies them itself gets the same document `stageStructural` does.
    const blocks = parseBlocks(BOARD);
    for (const op of [
      { kind: "add-row", block: 1 },
      { kind: "add-column", block: 1 },
      { kind: "delete-row", block: 1, row: 0 },
      { kind: "swap-blocks", first: 0, second: 1 },
    ] as const) {
      for (const edit of structuralEditsFor(BOARD, blocks, op)) {
        expect(edit.start).toBeGreaterThanOrEqual(0);
        expect(edit.end).toBeLessThanOrEqual(BOARD.length);
        expect(edit.end).toBeGreaterThanOrEqual(edit.start);
      }
    }
  });
});
