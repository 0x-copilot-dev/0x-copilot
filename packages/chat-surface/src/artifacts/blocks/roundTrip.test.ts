// The property everything else rests on: parsing a document and re-joining the
// blocks' spans returns the SAME STRING, and an edit changes only the span it
// names.
//
// These are written as properties over a corpus rather than as a handful of
// examples on purpose. A block scanner fails by leaving a byte behind — a blank
// line nobody owns, a fence that consumed one line too many — and a
// per-construct example test looks at what the scanner produced, not at what it
// dropped. Coverage is the assertion that can see the gap.

import { describe, expect, it } from "vitest";

import { generateDocument, MARKDOWN_CORPUS, mulberry32 } from "./corpus";
import { parseBlocks } from "./parseBlocks";
import {
  applyEdits,
  blockEdit,
  cellEdit,
  spliceBlock,
  spliceCell,
  spliceHeaderCell,
} from "./splice";
import type { DocumentBlock } from "./blockModel";

/** One plain word, so the reparse measures the SPLICE and not new markdown. */
const REPLACEMENT = "REPLACED";

function rejoin(source: string): string {
  return parseBlocks(source)
    .map((block) => source.slice(block.start, block.end))
    .join("");
}

function assertContiguous(source: string, blocks: DocumentBlock[]): void {
  let cursor = 0;
  for (const block of blocks) {
    expect(block.start).toBe(cursor);
    expect(block.end).toBeGreaterThan(block.start);
    cursor = block.end;
  }
  expect(cursor).toBe(source.length);
}

describe("parseBlocks round trip", () => {
  for (const [name, source] of Object.entries(MARKDOWN_CORPUS)) {
    it(`re-joins ${name} byte for byte`, () => {
      expect(rejoin(source)).toBe(source);
    });

    it(`covers ${name} with contiguous, non-overlapping blocks`, () => {
      assertContiguous(source, parseBlocks(source));
    });

    it(`reports every block's own text as its own slice for ${name}`, () => {
      for (const block of parseBlocks(source)) {
        if (block.kind === "table") {
          expect(block.headers).toEqual(
            block.headerCells.map((cell) => cell.text),
          );
          for (const row of [block.headerCells, ...block.rows]) {
            for (const cell of row) {
              expect(cell.text).toBe(source.slice(cell.start, cell.end));
              expect(cell.start).toBeGreaterThanOrEqual(block.start);
              expect(cell.end).toBeLessThanOrEqual(block.end);
            }
          }
          continue;
        }
        expect(block.text).toBe(source.slice(block.textStart, block.textEnd));
        expect(block.textStart).toBeGreaterThanOrEqual(block.start);
        expect(block.textEnd).toBeLessThanOrEqual(block.end);
      }
    });
  }

  it("re-joins 500 generated documents byte for byte", () => {
    const random = mulberry32(0x5eed);
    for (let iteration = 0; iteration < 500; iteration += 1) {
      const source = generateDocument(random);
      const rejoined = rejoin(source);
      // Compared explicitly so a failure prints the document that broke it.
      if (rejoined !== source) {
        throw new Error(
          `round trip lost bytes at iteration ${iteration}:\n${JSON.stringify(source)}\n!=\n${JSON.stringify(rejoined)}`,
        );
      }
      assertContiguous(source, parseBlocks(source));
    }
  });
});

describe("applyEdits identity and locality", () => {
  it("returns the source itself for an empty batch", () => {
    for (const source of Object.values(MARKDOWN_CORPUS)) {
      expect(applyEdits(source, [])).toBe(source);
    }
  });

  it("changes nothing outside a single edit's span", () => {
    const source = MARKDOWN_CORPUS.BUG_DOCUMENT;
    const table = parseBlocks(source).find((block) => block.kind === "table");
    if (table?.kind !== "table")
      throw new Error("no table in the bug document");
    const edit = cellEdit(table, 1, 2, "Urgent");
    const next = applyEdits(source, [edit]);

    expect(next.slice(0, edit.start)).toBe(source.slice(0, edit.start));
    expect(next.slice(edit.start + edit.text.length)).toBe(
      source.slice(edit.end),
    );
    expect(next.slice(edit.start, edit.start + edit.text.length)).toBe(
      "Urgent",
    );
  });

  // Every kind but `table` — a table has no whole-block span, only cells. The
  // `raw` blocks are the load-bearing half of this one: a list absorbs the
  // blank line beneath it into its FOOTPRINT, so an edit that spliced
  // `[start, end)` would delete that blank line and weld the list to the block
  // below. Asserting the tail is byte-identical is what catches that, and it is
  // asserted over all 22 documents rather than over one hand-picked list.
  it("leaves every other block's span untouched across the whole corpus", () => {
    for (const source of Object.values(MARKDOWN_CORPUS)) {
      for (const block of parseBlocks(source)) {
        if (block.kind === "table") continue;
        const edit = blockEdit(block, REPLACEMENT);
        const next = applyEdits(source, [edit]);
        const tail = source.length - block.textEnd;
        expect(next.slice(0, block.textStart)).toBe(
          source.slice(0, block.textStart),
        );
        expect(next.slice(next.length - tail)).toBe(
          source.slice(block.textEnd),
        );
        // What landed inside the span is the replacement, and — for a text-less
        // heading — the whitespace that keeps the `#` marker off it. Trimming to
        // the replacement is the assertion that the engine may add a SEPARATOR
        // and never anything else.
        expect(next.slice(block.textStart, next.length - tail)).toBe(edit.text);
        expect(edit.text.trim()).toBe(REPLACEMENT);
      }
    }
  });

  it("is the identity edit when a span is replaced by what it already holds", () => {
    for (const source of Object.values(MARKDOWN_CORPUS)) {
      const blocks = parseBlocks(source);
      for (const block of blocks) {
        if (block.kind === "table") {
          for (const [column, cell] of block.headerCells.entries()) {
            expect(spliceHeaderCell(source, block, column, cell.text)).toBe(
              source,
            );
          }
          for (const [row, cells] of block.rows.entries()) {
            for (const [column, cell] of cells.entries()) {
              expect(spliceCell(source, block, row, column, cell.text)).toBe(
                source,
              );
            }
          }
          continue;
        }
        expect(spliceBlock(source, block, block.text)).toBe(source);
      }
    }
  });
});

/**
 * The property `blockEdit` exists to hold, and the one a coverage test cannot
 * see: reading the document BACK after an edit finds the same block, holding
 * exactly what was written.
 *
 * A span replacement can be perfectly local and still corrupt the document.
 * `######` is a heading whose editable span is empty and sits hard against the
 * marker, so splicing `Summary` into it produced `######Summary` — one span
 * replaced, every other byte identical, and the heading silently became a
 * paragraph. `# ###` produced `# Summary###`, still a heading but now reading
 * `Summary###`. Locality was never the whole property; this is the rest of it.
 *
 * It is asserted for `heading` and `paragraph` only, and the exclusion is
 * principled rather than convenient: those are the kinds whose MARKER lies
 * outside the editable span. A `raw` block's span is the construct itself —
 * fence lines, `>` markers, a setext underline — so replacing it is meant to
 * change what the block is, and asking it to stay a fence would be asking the
 * text of a code block not to be editable.
 */
describe("an edited block reads back as itself", () => {
  const shapeOf = (blocks: readonly DocumentBlock[]): string[] =>
    blocks.map((block) =>
      block.kind === "raw" ? `raw:${block.reason}` : block.kind,
    );

  function assertEditReadsBack(source: string, label: string): void {
    const blocks = parseBlocks(source);
    const shape = shapeOf(blocks);
    blocks.forEach((block, index) => {
      if (block.kind !== "heading" && block.kind !== "paragraph") return;
      const next = spliceBlock(source, block, REPLACEMENT);
      const reparsed = parseBlocks(next);
      const detail = `${label} block ${index} (${block.kind}): ${JSON.stringify(source)} -> ${JSON.stringify(next)}`;
      // A plain word on purpose. Writing `# x` into a paragraph really does
      // make it a heading — that is a legal markdown edit the user asked for,
      // not corruption, so it would test the wrong thing here.
      if (shapeOf(reparsed).join() !== shape.join()) {
        throw new Error(
          `editing changed the document's block shape: ${detail}\n${shape.join()} -> ${shapeOf(reparsed).join()}`,
        );
      }
      const after = reparsed[index];
      if (after.kind !== block.kind) throw new Error(detail);
      if (after.text !== REPLACEMENT) {
        throw new Error(`edited block reads back as ${after.text}: ${detail}`);
      }
    });
  }

  for (const [name, source] of Object.entries(MARKDOWN_CORPUS)) {
    it(`survives editing every prose span of ${name}`, () => {
      assertEditReadsBack(source, name);
    });
  }

  it("survives editing every prose span of 500 generated documents", () => {
    const random = mulberry32(0x5eed);
    for (let iteration = 0; iteration < 500; iteration += 1) {
      assertEditReadsBack(generateDocument(random), `iteration ${iteration}`);
    }
  });
});
