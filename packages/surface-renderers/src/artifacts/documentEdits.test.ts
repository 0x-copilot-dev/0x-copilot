import { applyEdits, parseBlocks } from "@0x-copilot/chat-surface";
import { describe, expect, it } from "vitest";

import {
  commitEdit,
  documentEditFor,
  documentEditsFor,
  nextCellTarget,
  originalValue,
  revertEdit,
  targetKey,
  type EditTarget,
  type PendingEdits,
} from "./documentEdits";

const RAGGED = [
  "| A | B | C |",
  "| --- | --- | --- |",
  "| one | two |",
  "| x | y | z |",
  "",
].join("\n");

/**
 * One shape per construct the block model routes to `raw`, each followed by a
 * block the edit must not reach. Every one of them absorbs the blank line
 * beneath it into its FOOTPRINT, which is exactly the byte an edit spliced
 * against `[start, end)` would delete.
 */
const SHAPES: Record<string, string> = {
  checklistOnly: "- [ ] Draft the memo\n- [ ] Send it to Ana\n",
  listThenProse: "- one\n- two\n\nProse under the list.\n",
  fenceThenProse:
    "```js\nconst pipes = '| a | b |';\n```\n\nAfter the fence.\n",
  quoteThenHeading: "> quoted line\n\n## Next section\n",
  breakThenProse: "---\n\nBody under the divider.\n",
  setextThenProse: "Setext title\n===\n\nBody.\n",
  headingThenList: "# Title\n\n- one\n- two\n\nEnd.\n",
  leadingBlank: "\n\n# Title\n\n- only item\n",
};

describe("documentEdits", () => {
  it("walks a ragged table by the cells that exist, never by row × column arithmetic", () => {
    const blocks = parseBlocks(RAGGED);
    const headerLast: EditTarget = { kind: "header", block: 0, column: 2 };
    // Header row, then row 0's TWO cells, then row 1's three.
    const first = nextCellTarget(blocks, headerLast, 1);
    expect(first).toEqual({ kind: "cell", block: 0, row: 0, column: 0 });
    const second = nextCellTarget(blocks, first!, 1);
    expect(second).toEqual({ kind: "cell", block: 0, row: 0, column: 1 });
    // The ragged row has no third cell — arithmetic would land on one that has
    // no span to splice into. The next cell is the next row's first.
    expect(nextCellTarget(blocks, second!, 1)).toEqual({
      kind: "cell",
      block: 0,
      row: 1,
      column: 0,
    });
    // Both ends stop rather than wrapping into another block.
    expect(
      nextCellTarget(blocks, { kind: "header", block: 0, column: 0 }, -1),
    ).toBeNull();
    expect(
      nextCellTarget(blocks, { kind: "cell", block: 0, row: 1, column: 2 }, 1),
    ).toBeNull();
  });

  it("shows a cell's escaped pipe as the pipe it stands for, and writes it back escaped", () => {
    const source = "| A | B |\n| --- | --- |\n| x \\| y | z |\n";
    const blocks = parseBlocks(source);
    const target: EditTarget = { kind: "cell", block: 0, row: 0, column: 0 };
    expect(originalValue(blocks, target)).toBe("x | y");
    // Round-tripping an untouched cell is byte-identical: the escape is not
    // re-escaped, and nothing else in the row moves.
    expect(applyEdits(source, [documentEditFor(blocks, target, "x | y")])).toBe(
      source,
    );
  });

  it("keeps a heading on one line, because an ATX heading that gains a newline stops being one", () => {
    const source = "# Title\n\nbody\n";
    const blocks = parseBlocks(source);
    const target: EditTarget = { kind: "prose", block: 0 };
    expect(
      applyEdits(source, [documentEditFor(blocks, target, "New\ntitle")]),
    ).toBe("# New title\n\nbody\n");
    // A paragraph is written back verbatim — its line breaks are the author's.
    expect(
      applyEdits(source, [
        documentEditFor(blocks, { kind: "prose", block: 1 }, "one\ntwo"),
      ]),
    ).toBe("# Title\n\none\ntwo\n");
  });

  it("forgets an edit that types the original back, and restores the prior one on revert", () => {
    const blocks = parseBlocks(RAGGED);
    const target: EditTarget = { kind: "cell", block: 0, row: 0, column: 1 };
    const key = targetKey(target);
    const typed = commitEdit({}, blocks, target, "TWO");
    expect(Object.keys(typed)).toEqual([key]);
    expect(commitEdit(typed, blocks, target, "two")).toEqual({});
    // Escape after a second session restores what the first one left.
    const second = commitEdit(typed, blocks, target, "2");
    expect(revertEdit(second, target, typed[key])).toEqual(typed);
    expect(revertEdit(second, target, undefined)).toEqual({});
  });

  it("throws on a target the document no longer has, rather than dropping what a user typed", () => {
    const blocks = parseBlocks(RAGGED);
    const stale: PendingEdits = {
      "c:0:9:0": {
        target: { kind: "cell", block: 0, row: 9, column: 0 },
        value: "typed",
      },
    };
    expect(() => documentEditsFor(blocks, stale)).toThrow(RangeError);
    expect(() =>
      documentEditFor(blocks, { kind: "prose", block: 0 }, "x"),
    ).toThrow(/not editable prose/);
    expect(() =>
      documentEditFor(
        blocks,
        { kind: "cell", block: 4, row: 0, column: 0 },
        "x",
      ),
    ).toThrow(/No block at index 4/);
  });

  it("edits a raw block through the same primitive, and keeps the blank line it absorbed", () => {
    const source = SHAPES.listThenProse;
    const blocks = parseBlocks(source);
    const target: EditTarget = { kind: "prose", block: 0 };

    // The list is one block whose value is its own lines — no per-item control,
    // because nothing in the block model claims to understand a list item.
    expect(originalValue(blocks, target)).toBe("- one\n- two");
    // Adding a line is a legal edit of that text; the paragraph below keeps the
    // blank line that separates it, so the two do not become one block.
    expect(
      applyEdits(source, [
        documentEditFor(blocks, target, "- one\n- two\n- 3"),
      ]),
    ).toBe("- one\n- two\n- 3\n\nProse under the list.\n");
    // Round-tripping the untouched value is byte-identical, which is the same
    // statement made from the other end.
    expect(
      applyEdits(source, [documentEditFor(blocks, target, "- one\n- two")]),
    ).toBe(source);
  });

  it("refuses a blank run, which is the whitespace between blocks and has no control", () => {
    const blocks = parseBlocks(SHAPES.leadingBlank);
    const target: EditTarget = { kind: "prose", block: 0 };
    expect(blocks[0]).toMatchObject({ kind: "raw", reason: "blank" });
    expect(originalValue(blocks, target)).toBeNull();
    expect(() => documentEditFor(blocks, target, "typed")).toThrow(
      /not editable prose \(raw blank\)/,
    );
  });

  it("replaces exactly one block's own span, for every editable block of every shape", () => {
    for (const [name, source] of Object.entries(SHAPES)) {
      parseBlocks(source).forEach((block, index) => {
        if (block.kind === "table") return;
        if (block.kind === "raw" && block.reason === "blank") return;
        const blocks = parseBlocks(source);
        const next = applyEdits(source, [
          documentEditFor(blocks, { kind: "prose", block: index }, "REPLACED"),
        ]);
        // Everything before the block's TEXT and everything after it is copied
        // through — including the blank line the block absorbed into its
        // footprint, which is the byte that stops an edited list being welded
        // to whatever follows it.
        expect(`${name}[${index}] ${next}`).toBe(
          `${name}[${index}] ${source.slice(0, block.textStart)}REPLACED${source.slice(block.textEnd)}`,
        );
      });
    }
  });

  it("applies a whole batch against the original offsets, in any order", () => {
    const blocks = parseBlocks(RAGGED);
    const pending: PendingEdits = {
      "c:0:1:2": {
        target: { kind: "cell", block: 0, row: 1, column: 2 },
        value: "ZED",
      },
      "h:0:0": { target: { kind: "header", block: 0, column: 0 }, value: "AY" },
      "c:0:0:0": {
        target: { kind: "cell", block: 0, row: 0, column: 0 },
        value: "ONE",
      },
    };
    expect(applyEdits(RAGGED, documentEditsFor(blocks, pending))).toBe(
      [
        "| AY | B | C |",
        "| --- | --- | --- |",
        "| ONE | two |",
        "| x | y | ZED |",
        "",
      ].join("\n"),
    );
  });
});
