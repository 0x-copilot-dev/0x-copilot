// Splicing: the edited span changes, and nothing else does.
//
// Each assertion below is written against the WHOLE resulting document rather
// than against the edited cell, because "did the edit land" is the easy half.
// The half that matters is that the heading above it, the prose under it, the
// links in the neighbouring cells and the table's own padding all came through
// untouched — which only a full-string comparison can show.

import { describe, expect, it } from "vitest";

import {
  BUG_DOCUMENT,
  EMPTY_CELLS_TABLE,
  ESCAPED_PIPE_TABLE,
  RAGGED_TABLE,
} from "./corpus";
import { parseBlocks } from "./parseBlocks";
import {
  applyEdits,
  blockEdit,
  cellEdit,
  headerCellEdit,
  spliceBlock,
  spliceCell,
  spliceHeaderCell,
} from "./splice";
import { escapeTableCellText, unescapeTableCellText } from "./tables";
import type {
  EditableBlock,
  HeadingBlock,
  ParagraphBlock,
  TableBlock,
} from "./blockModel";

function tableOf(source: string): TableBlock {
  const table = parseBlocks(source).find(
    (block): block is TableBlock => block.kind === "table",
  );
  if (table === undefined) throw new Error("expected a table block");
  return table;
}

function headingOf(source: string): HeadingBlock {
  const heading = parseBlocks(source).find(
    (block): block is HeadingBlock => block.kind === "heading",
  );
  if (heading === undefined) throw new Error("expected a heading block");
  return heading;
}

function paragraphOf(source: string): ParagraphBlock {
  const paragraph = parseBlocks(source).find(
    (block): block is ParagraphBlock => block.kind === "paragraph",
  );
  if (paragraph === undefined) throw new Error("expected a paragraph block");
  return paragraph;
}

describe("spliceCell", () => {
  it("replaces one cell and leaves the padding and the row's other cells alone", () => {
    const next = spliceCell(BUG_DOCUMENT, tableOf(BUG_DOCUMENT), 1, 1, "Warm");
    expect(next).toBe(
      BUG_DOCUMENT.replace(
        "| [PAR-12 – Sync retry backoff](https://linear.app/parth/issue/PAR-12) | Cool | Medium |",
        "| [PAR-12 – Sync retry backoff](https://linear.app/parth/issue/PAR-12) | Warm | Medium |",
      ),
    );
    // Only the four bytes of "Cool" moved: the other two rows still say it.
    expect(next.match(/Cool/g)).toHaveLength(3);
  });

  it("writes markdown into a cell verbatim, link and all", () => {
    const link = "[PAR-9 – Renamed](https://linear.app/parth/issue/PAR-9)";
    const next = spliceCell(BUG_DOCUMENT, tableOf(BUG_DOCUMENT), 0, 0, link);
    expect(next).toContain(`| ${link} | Cool | High |`);
    expect(next).toContain("All five issues are currently in **Cool** status.");
  });

  it("splices into an empty cell without disturbing its padding", () => {
    const table = tableOf(EMPTY_CELLS_TABLE);
    expect(spliceCell(EMPTY_CELLS_TABLE, table, 0, 0, "x")).toContain(
      "| x |  |",
    );
  });

  it("refuses a column a ragged row does not have", () => {
    const table = tableOf(RAGGED_TABLE);
    expect(() => spliceCell(RAGGED_TABLE, table, 1, 2, "z")).toThrow(
      RangeError,
    );
    expect(() => spliceCell(RAGGED_TABLE, table, 9, 0, "z")).toThrow(
      RangeError,
    );
    // The row that DOES have a third cell still splices.
    expect(spliceCell(RAGGED_TABLE, table, 2, 3, "ten")).toContain(
      "| 6 | 7 | 8 | ten |",
    );
  });

  it("preserves an escaped pipe in a neighbouring cell", () => {
    const table = tableOf(ESCAPED_PIPE_TABLE);
    const next = spliceCell(ESCAPED_PIPE_TABLE, table, 0, 1, "bitwise OR");
    expect(next).toContain("| `a \\| b` | bitwise OR |");
    expect(next).toContain("| plain | nothing \\| special |");
  });
});

describe("spliceHeaderCell and spliceBlock", () => {
  it("renames a column without touching the body", () => {
    const next = spliceHeaderCell(BUG_DOCUMENT, tableOf(BUG_DOCUMENT), 2, "P");
    expect(next).toContain("| Issue | Status | P |");
    expect(next).toContain("| --- | --- | --- |");
    expect(next).toContain("| Cool | High |");
  });

  it("replaces prose without swallowing the blank line under it", () => {
    const paragraph = paragraphOf(BUG_DOCUMENT);
    const next = spliceBlock(BUG_DOCUMENT, paragraph, "Three are **Cool**.");
    expect(next).toBe(
      BUG_DOCUMENT.replace(
        "All five issues are currently in **Cool** status.",
        "Three are **Cool**.",
      ),
    );
    expect(next.endsWith("Three are **Cool**.\n")).toBe(true);
  });

  it("keeps the heading's marker when its text is replaced", () => {
    const heading = parseBlocks(BUG_DOCUMENT)[0] as EditableBlock;
    expect(spliceBlock(BUG_DOCUMENT, heading, "My Linear queue")).toContain(
      "# My Linear queue\n\n| Issue |",
    );
  });
});

describe("filling in a heading that has no text", () => {
  const fill = (source: string, next: string): string =>
    spliceBlock(source, headingOf(source), next);

  it("does not weld the text to the opening marker", () => {
    // `######Summary` renders as a bold line of prose, not a heading — the
    // splice was local and the document was still corrupted.
    expect(fill("######\n", "Summary")).toBe("###### Summary\n");
    expect(fill("#\n", "Summary")).toBe("# Summary\n");
    expect(parseBlocks(fill("######\n", "Summary"))[0].kind).toBe("heading");
  });

  it("does not weld the text to a closing marker", () => {
    // `# Summary###` stays a heading and reads `Summary###`: the closing run
    // the user never touched became part of their text.
    expect(fill("# ###\n", "Summary")).toBe("# Summary ###\n");
    expect(headingOf(fill("# ###\n", "Summary")).text).toBe("Summary");
  });

  it("adds nothing when the source already separates the span", () => {
    expect(fill("# \n", "Summary")).toBe("# Summary\n");
    expect(fill("#\t\n", "Summary")).toBe("#\tSummary\n");
    expect(fill("#   \n", "Summary")).toBe("#   Summary\n");
  });

  it("writes nothing at all when the replacement is empty", () => {
    // Both directions of the empty case: clearing a heading that had text, and
    // "clearing" one that never had any. Neither may leave a stray space.
    expect(fill("######\n", "")).toBe("######\n");
    expect(fill("# ###\n", "")).toBe("# ###\n");
    expect(fill("# Title ###\n", "")).toBe("#  ###\n");
    expect(headingOf(fill("# Title ###\n", "")).text).toBe("");
  });

  it("still refuses to reformat the text it is given", () => {
    // The separator is the ONLY thing the engine adds. Inline markdown, a
    // trailing hash that is not a closing run, a link — all verbatim.
    expect(fill("######\n", "**Bold** [a](b)")).toBe(
      "###### **Bold** [a](b)\n",
    );
    expect(fill("######\n", "C#")).toBe("###### C#\n");
    expect(headingOf(fill("######\n", "C#")).text).toBe("C#");
  });
});

describe("applyEdits batching", () => {
  it("applies three edits spanning two blocks in one call", () => {
    const table = tableOf(BUG_DOCUMENT);
    const paragraph = paragraphOf(BUG_DOCUMENT);
    const next = applyEdits(BUG_DOCUMENT, [
      cellEdit(table, 2, 2, "Urgent"),
      headerCellEdit(table, 1, "State"),
      blockEdit(paragraph, "Three issues remain."),
    ]);
    expect(next).toBe(
      BUG_DOCUMENT.replace(
        "| Issue | Status | Priority |",
        "| Issue | State | Priority |",
      )
        .replace("| Cool | Low |", "| Cool | Urgent |")
        .replace(
          "All five issues are currently in **Cool** status.",
          "Three issues remain.",
        ),
    );
  });

  it("does not depend on the order the batch arrives in", () => {
    const table = tableOf(BUG_DOCUMENT);
    const paragraph = paragraphOf(BUG_DOCUMENT);
    const edits = [
      cellEdit(table, 0, 1, "Hot"),
      cellEdit(table, 2, 1, "Cold"),
      blockEdit(paragraph, "Mixed."),
    ];
    const forwards = applyEdits(BUG_DOCUMENT, edits);
    expect(applyEdits(BUG_DOCUMENT, [...edits].reverse())).toBe(forwards);
    expect(forwards).toContain("Hot");
    expect(forwards).toContain("Cold");
  });

  it("never mutates the caller's batch", () => {
    const table = tableOf(BUG_DOCUMENT);
    const edits = [cellEdit(table, 2, 0, "c"), cellEdit(table, 0, 0, "a")];
    const snapshot = [...edits];
    applyEdits(BUG_DOCUMENT, edits);
    expect(edits).toEqual(snapshot);
  });

  it("throws on two edits over one span rather than silently dropping one", () => {
    const table = tableOf(BUG_DOCUMENT);
    expect(() =>
      applyEdits(BUG_DOCUMENT, [
        cellEdit(table, 0, 0, "first"),
        cellEdit(table, 0, 0, "second"),
      ]),
    ).toThrow(/overlap/);
  });

  it("throws on a span that is not inside the document", () => {
    expect(() =>
      applyEdits("short", [{ start: 2, end: 99, text: "x" }]),
    ).toThrow(RangeError);
    expect(() =>
      applyEdits("short", [{ start: 4, end: 1, text: "x" }]),
    ).toThrow(RangeError);
  });

  it("re-parses after an edit into the same block shape", () => {
    const next = applyEdits(BUG_DOCUMENT, [
      cellEdit(tableOf(BUG_DOCUMENT), 0, 0, "[PAR-9](https://linear.app/x)"),
    ]);
    expect(parseBlocks(next).map((block) => block.kind)).toEqual([
      "heading",
      "table",
      "paragraph",
    ]);
    expect(tableOf(next).rows[0][0].text).toBe("[PAR-9](https://linear.app/x)");
  });
});

describe("table cell escaping", () => {
  it("escapes the two characters that would break a row, and nothing else", () => {
    expect(escapeTableCellText("a | b")).toBe("a \\| b");
    expect(escapeTableCellText("one\ntwo")).toBe("one two");
    expect(escapeTableCellText("**bold** and [a](b)")).toBe(
      "**bold** and [a](b)",
    );
  });

  it("is idempotent, so saving twice cannot pile up backslashes", () => {
    const once = escapeTableCellText("a | b");
    expect(escapeTableCellText(once)).toBe(once);
  });

  it("round-trips back to what the user typed", () => {
    expect(unescapeTableCellText(escapeTableCellText("a | b"))).toBe("a | b");
    expect(unescapeTableCellText("`a \\| b` \\*keep\\*")).toBe(
      "`a | b` \\*keep\\*",
    );
  });

  it("keeps a table parseable after a pipe is typed into a cell", () => {
    const table = tableOf(BUG_DOCUMENT);
    const typed = "Cool | Blocked";
    const next = spliceCell(
      BUG_DOCUMENT,
      table,
      0,
      1,
      escapeTableCellText(typed),
    );
    const reparsed = tableOf(next);
    expect(reparsed.rows[0]).toHaveLength(3);
    expect(unescapeTableCellText(reparsed.rows[0][1].text)).toBe(typed);
  });
});
