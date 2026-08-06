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

import { CORPUS_FRAGMENTS, MARKDOWN_CORPUS } from "./corpus";
import { parseBlocks } from "./parseBlocks";
import { applyEdits, blockEdit, cellEdit } from "./splice";
import type { DocumentBlock } from "./blockModel";

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

/** Deterministic PRNG so a failing generated document is reproducible from the seed. */
function mulberry32(seed: number): () => number {
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

function generateDocument(random: () => number): string {
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
        if (block.kind === "raw") {
          expect(block.text).toBe(source.slice(block.start, block.end));
          continue;
        }
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

  it("leaves every other block's span untouched across the whole corpus", () => {
    for (const source of Object.values(MARKDOWN_CORPUS)) {
      for (const block of parseBlocks(source)) {
        if (block.kind !== "paragraph" && block.kind !== "heading") continue;
        const edit = blockEdit(block, "REPLACED");
        const next = applyEdits(source, [edit]);
        expect(next).toBe(
          `${source.slice(0, block.textStart)}REPLACED${source.slice(block.textEnd)}`,
        );
      }
    }
  });
});
