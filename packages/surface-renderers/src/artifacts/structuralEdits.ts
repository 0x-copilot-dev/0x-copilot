// A structural change, staged next to the text edits it has to live with.
//
// `documentEdits.ts` turns a pending TEXT edit into the span it replaces. This
// module is the other half: the change that alters WHICH spans a document has —
// a row appears, a column goes away, two blocks trade places. It computes no
// span of its own. Every operation delegates to the builders in
// `@0x-copilot/chat-surface` (`addRowEdits`, `deleteColumnEdits`,
// `swapBlocksEdits`, …), which are the only code that decides where a line ends
// or what a delimiter row must look like.
//
// The hard part is not the edits. It is that a structural change and a cell edit
// must be able to be pending AT THE SAME TIME, and those two are not expressible
// in one batch: `applyEdits` throws on overlapping spans, and "delete row 2" is
// a span that CONTAINS the cell span "row 2, column 1". A throw reaching a user
// is a crash, not a guard — so the composition is fixed in the representation
// rather than defended against at the end:
//
//   **A structural change is spliced immediately, into a working document; text
//   edits stay pending against that working document.** Two stages, never one
//   batch, so the two kinds of edit can no more overlap than two edits of
//   different documents can. Save is `applyEdits(workingDocument, textEdits)`,
//   and Discard throws the working document away and goes back to the artifact's
//   own source. Nothing reaches the host until Save, whichever kind of change it
//   was.
//
// What that costs is one piece of bookkeeping, and it is the thing to be careful
// about: splicing a row out MOVES the rows under it, so a pending edit addressed
// at "row 3" now means row 2. `remapTarget` re-addresses each pending edit, and
// `stageStructural` then CHECKS the result — every carried edit must still find
// a span holding exactly the value it found before, because no operation here
// changes the content of a span it did not remove. A structural edit that cannot
// be verified is refused whole rather than applied over edits it would silently
// misplace; the block model's own reparse is what has the final word, not this
// module's index arithmetic.

import {
  addBlockEdits,
  addColumnEdits,
  addRowEdits,
  applyEdits,
  deleteBlockEdits,
  deleteColumnEdits,
  deleteRowEdits,
  parseBlocks,
  swapBlocksEdits,
  type DocumentBlock,
  type DocumentEdit,
  type TableBlock,
} from "@0x-copilot/chat-surface";

import {
  originalValue,
  targetKey,
  type EditTarget,
  type PendingEdit,
  type PendingEdits,
} from "./documentEdits";

/** What an "add a block" control inserts. The module authors the markdown. */
export type NewBlockTemplate = "paragraph" | "table";

/**
 * One structural change, named by INTENT and by the indices the user sees.
 *
 * Every index addresses the document currently on screen — the same block, row
 * and column numbers the renderer drew its controls from. Nothing here carries
 * an offset: a caller that had to compute one would be deciding where a line
 * ends, which is the block model's job and not a renderer's.
 */
export type StructuralOp =
  | { readonly kind: "add-row"; readonly block: number }
  | {
      readonly kind: "insert-row";
      readonly block: number;
      readonly row: number;
    }
  | {
      readonly kind: "delete-row";
      readonly block: number;
      readonly row: number;
    }
  | { readonly kind: "add-column"; readonly block: number }
  | {
      readonly kind: "delete-column";
      readonly block: number;
      readonly column: number;
    }
  | {
      readonly kind: "add-block";
      readonly boundary: number;
      readonly template: NewBlockTemplate;
    }
  | { readonly kind: "delete-block"; readonly block: number }
  | {
      readonly kind: "swap-blocks";
      readonly first: number;
      readonly second: number;
    };

/** The working document a staged change produced, and where the text edits went. */
export interface StagedDocument {
  /** The previous working document with the change spliced in. */
  readonly source: string;
  /** The pending text edits, re-addressed against `source`. */
  readonly pending: PendingEdits;
}

const UNADDRESSABLE =
  "This change cannot be staged on top of the current unsaved edits: one of them no longer addresses the span it was typed into.";

const LEADING_TERMINATOR = /^(?:\r\n|\n|\r)/;
const ANY_TERMINATOR = /\r\n|\n|\r/;

const NEW_PARAGRAPH = "New paragraph";
const NEW_TABLE_LINES = ["| Column 1 | Column 2 |", "| --- | --- |", "|  |  |"];

/**
 * The document's own line ending, for the one thing this module WRITES rather
 * than splices: the markdown of a brand-new block.
 *
 * Reading it here is not the renderer measuring a span — it is the same rule
 * `structure.ts` states for every row it inserts (a CRLF document stays a CRLF
 * document), applied to text that does not exist in the document yet and so has
 * no builder to inherit it from.
 */
function lineEnding(source: string): string {
  const match = ANY_TERMINATOR.exec(source);
  return match === null ? "\n" : match[0];
}

/**
 * The markdown a new block starts life as.
 *
 * A paragraph cannot start empty — blank lines are absorbed by the block above
 * them, so an "empty paragraph" is whitespace and no block at all, and
 * `addBlockEdits` refuses it. It therefore starts as a word the user types over,
 * which is also what makes it visible enough to find and delete again.
 *
 * A table starts as the smallest thing that IS a table: a header, the delimiter
 * row that declares its columns, and one empty body row to type into.
 */
function newBlockText(template: NewBlockTemplate, source: string): string {
  return template === "paragraph"
    ? NEW_PARAGRAPH
    : NEW_TABLE_LINES.join(lineEnding(source));
}

function tableAt(blocks: readonly DocumentBlock[], index: number): TableBlock {
  const block = blocks[index];
  if (block === undefined || block.kind !== "table") {
    throw new RangeError(`Block ${index} is not a table`);
  }
  return block;
}

/**
 * Inserting a row after a given one, out of the two builders that already know
 * where that row begins and ends.
 *
 * The block package names one row insertion, `addRowEdits`, and it appends. The
 * missing sibling is `insertRowEdits(source, block, afterRow)` and it belongs
 * THERE — this file measures no lines, so rather than scan for one it reads the
 * two facts it needs off the builders that own them:
 *
 *   * `deleteRowEdits(source, block, row)` names the span that row occupies,
 *     terminator included. Its END is exactly where the next row starts, and it
 *     range-checks `row` on the way past.
 *   * `addRowEdits(source, block)` names the row TEXT this table writes new rows
 *     in — the header's column count, the anchor row's indentation, the
 *     document's own line ending. None of that is a renderer's to invent.
 *
 * What is left to decide is which side of the new row the terminator goes on,
 * and the insertion POINT decides it rather than taste. At a line start — every
 * row but a last one in a document that stops without a newline — the row is
 * written `row + eol`. At the end of such a document there is no line start to
 * write at, so the terminator comes first; that case is `addRowEdits`' own edit
 * unchanged, because there "after the last row" and "at the end" are one place.
 */
function insertRowAfterEdits(
  source: string,
  block: TableBlock,
  rowIndex: number,
): DocumentEdit[] {
  const appended = addRowEdits(source, block);
  const removed = deleteRowEdits(source, block, rowIndex);
  const at = removed[removed.length - 1].end;
  if (at === source.length) return appended;
  const written = appended[0].text;
  const leading = LEADING_TERMINATOR.exec(written);
  return [
    {
      start: at,
      end: at,
      text:
        leading === null
          ? written
          : `${written.slice(leading[0].length)}${leading[0]}`,
    },
  ];
}

/**
 * The span replacements one structural change becomes, against `source`.
 *
 * Exported for the tests that read an operation's edits directly; the component
 * goes through `stageStructural`, which is the only path that also carries the
 * pending text edits across.
 */
export function structuralEditsFor(
  source: string,
  blocks: readonly DocumentBlock[],
  op: StructuralOp,
): readonly DocumentEdit[] {
  switch (op.kind) {
    case "add-row":
      return addRowEdits(source, tableAt(blocks, op.block));
    case "insert-row":
      return insertRowAfterEdits(source, tableAt(blocks, op.block), op.row);
    case "delete-row":
      return deleteRowEdits(source, tableAt(blocks, op.block), op.row);
    case "add-column":
      return addColumnEdits(source, tableAt(blocks, op.block));
    case "delete-column":
      return deleteColumnEdits(source, tableAt(blocks, op.block), op.column);
    case "add-block":
      return addBlockEdits(
        source,
        blocks,
        op.boundary,
        newBlockText(op.template, source),
      );
    case "delete-block":
      return deleteBlockEdits(source, blocks, op.block);
    case "swap-blocks":
      return swapBlocksEdits(source, blocks, op.first, op.second);
  }
}

function withBlock(target: EditTarget, block: number): EditTarget {
  switch (target.kind) {
    case "cell":
      return { kind: "cell", block, row: target.row, column: target.column };
    case "header":
      return { kind: "header", block, column: target.column };
    case "prose":
      return { kind: "prose", block };
  }
}

/**
 * Where a pending text edit's target moves when a structural change lands, or
 * `null` when the span it addressed is being removed.
 *
 * `null` is the correct answer and not a dropped edit: the row, column or block
 * that carried the typed value is the one the user just asked to delete, and it
 * left the screen when the change was staged. Everything else is a shift by one
 * in exactly one coordinate — which is index arithmetic, and index arithmetic is
 * not trusted here. `stageStructural` verifies every answer against the
 * document's own reparse.
 */
export function remapTarget(
  op: StructuralOp,
  target: EditTarget,
): EditTarget | null {
  switch (op.kind) {
    // Both APPEND — a new row after the last one, a new cell at each row's own
    // end — so no existing row or column index moves.
    case "add-row":
    case "add-column":
      return target;
    case "insert-row":
      return target.kind === "cell" &&
        target.block === op.block &&
        target.row > op.row
        ? {
            kind: "cell",
            block: op.block,
            row: target.row + 1,
            column: target.column,
          }
        : target;
    case "delete-row":
      if (target.kind !== "cell" || target.block !== op.block) return target;
      if (target.row === op.row) return null;
      return target.row > op.row
        ? {
            kind: "cell",
            block: op.block,
            row: target.row - 1,
            column: target.column,
          }
        : target;
    case "delete-column":
      // A header cell and a body cell both live in a column; a prose block does
      // not, and a table's columns say nothing about any other block's.
      if (target.kind === "prose" || target.block !== op.block) return target;
      if (target.column === op.column) return null;
      if (target.column < op.column) return target;
      return target.kind === "header"
        ? { kind: "header", block: op.block, column: target.column - 1 }
        : {
            kind: "cell",
            block: op.block,
            row: target.row,
            column: target.column - 1,
          };
    case "add-block":
      return target.block >= op.boundary
        ? withBlock(target, target.block + 1)
        : target;
    case "delete-block":
      if (target.block === op.block) return null;
      return target.block > op.block
        ? withBlock(target, target.block - 1)
        : target;
    case "swap-blocks":
      if (target.block === op.first) return withBlock(target, op.second);
      if (target.block === op.second) return withBlock(target, op.first);
      return target;
  }
}

/**
 * Splices one structural change into the working document and carries the
 * pending text edits across it.
 *
 * The check on the far side is the load-bearing part. No operation here changes
 * the CONTENT of a span it did not remove — a deleted row takes whole lines, a
 * new column is appended at each row's own end, a moved block's bytes travel
 * with it — so every carried edit must still find its span holding exactly the
 * value it held before. When one does not, the block model has reparsed the
 * document into a shape the index arithmetic did not predict (a leading blank
 * run absorbed into an inserted paragraph is the one this was written for), and
 * the whole change is refused rather than applied over a misplaced edit.
 *
 * Throws `RangeError` either way: for an operation the block package itself
 * rejects, and for a remap it could not verify.
 */
export function stageStructural(
  source: string,
  blocks: readonly DocumentBlock[],
  pending: PendingEdits,
  op: StructuralOp,
): StagedDocument {
  const next = applyEdits(source, structuralEditsFor(source, blocks, op));
  const nextBlocks = parseBlocks(next);
  const carried: Record<string, PendingEdit> = {};
  for (const edit of Object.values(pending)) {
    const target = remapTarget(op, edit.target);
    if (target === null) continue;
    const before = originalValue(blocks, edit.target);
    const after = originalValue(nextBlocks, target);
    if (before === null || after === null || before !== after) {
      throw new RangeError(UNADDRESSABLE);
    }
    carried[targetKey(target)] = { target, value: edit.value };
  }
  return { source: next, pending: carried };
}
