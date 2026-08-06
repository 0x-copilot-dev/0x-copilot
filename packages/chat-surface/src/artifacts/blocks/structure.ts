// Structural edits: the operations that change WHICH spans a document has.
//
// `splice.ts` writes a new value into a span that already exists — a cell, a
// heading's text. The operations here change the SET of spans: a row appears, a
// column goes away, two blocks trade places. The rule does not change with them.
//
// SPLICE, NEVER REGENERATE. Nothing below re-emits a table, re-indents a
// document, or normalizes a row it was not asked about. What each operation
// computes is the exact span to write, and for a structural edit that span is
// either ZERO-WIDTH — the point a new row goes at — or SEVERAL SPANS AT ONCE,
// because a column exists once per row and adding one is a batch of insertions
// that must land together.
//
// Every function returns `DocumentEdit[]` against the ORIGINAL source, so a
// structural edit batches with a text edit in one `applyEdits` call and there is
// exactly one application path. `applyEdits` throws on overlapping edits; each
// operation here emits at most one edit per LINE, with one exception that is
// adjacent rather than overlapping: a column deletion may add a zero-width
// insertion at a row's own opening, which is at or before the span it deletes
// on that line (see `openingPipeEdit`).
//
// Four invariants hold across the file, and each one names a way a document gets
// destroyed silently rather than loudly:
//
//  1. **Every row of a table still reads as a row.** A table whose header has
//     four cells and whose delimiter has three is not a table any more — it
//     reparses as prose, and every cell span in it is gone. Adding a column
//     therefore writes the delimiter row in the SAME batch as the header, never
//     as a follow-up edit that could be dropped or reordered. Deleting one is
//     the same rule from the other side, and it reaches past the delimiter row:
//     the cut can take the character a line STARTS with, and whatever the next
//     cell held becomes the head of the line — `---` is then a thematic break,
//     ` -` is a list, and either ends the table where it stands. Every row that
//     would stop reading as a row gets a pipe put back at its opening, in the
//     same batch.
//  2. **Line endings are the document's own.** A row inserted into a CRLF
//     document ends with CRLF, read off the table's own header line rather than
//     assumed.
//  3. **The final newline is neither invented nor lost.** A document that ends
//     without one still ends without one after every operation here; one that
//     ends with one keeps exactly one. Two cases cannot hold it and are named
//     where they arise rather than hidden: deleting the ONLY block leaves `""`,
//     the empty document, which has no newline to end with; and swapping the
//     last block with a CONTENT-LESS one — a document's leading run of blank
//     lines is a block — moves the last content away, leaving nothing for
//     "ends without a newline" to live on.
//  4. **Two blocks brought together get a blank line between them.** Without it
//     a paragraph swallows the block moved above it and a blockquote lazily
//     continues into the text moved under it. The merge is silent: the block
//     model just reports one block where there were two. Where the document
//     already separates them, nothing is added — the guarantee costs a blank
//     line only at a boundary the operation itself created.

import type { DocumentBlock, DocumentEdit, TableBlock } from "./blockModel";
import {
  isBlankLine,
  isSpaceChar,
  splitLines,
  startsBlock,
  type Line,
} from "./lines";
import {
  readDelimiterRow,
  rowSlots,
  splitRowCells,
  unescapedPipePositions,
  type RowSlot,
} from "./tables";

const LINE_TERMINATOR = /\r\n|\n|\r/;
const LINE_TERMINATORS = /\r\n|\n|\r/g;
const FINAL_TERMINATOR = /(?:\r\n|\n|\r)$/;

const STALE_TABLE =
  "This table block does not describe this document. Re-parse the source before building edits from it.";
const STALE_BLOCKS =
  "These blocks do not cover this document. Re-parse the source before building edits from them.";

// --- table geometry ---------------------------------------------------------

/** One line of a table, with the slots its pipes cut it into. */
interface TableRow {
  readonly line: Line;
  readonly slots: readonly RowSlot[];
}

/**
 * Where a table's lines are, which `TableBlock` does not say.
 *
 * The block carries cell spans, not line spans, and it carries nothing at all
 * about the delimiter row — that row has no editable cell, so the model has no
 * reason to hold it. A structural edit needs both, and it re-derives them from
 * the source rather than widening the model, because the answer has to come from
 * the string being edited: a block built from a stale string would otherwise
 * hand back offsets into a document that no longer exists.
 */
interface TableGeometry {
  /** The terminator this table's own lines use — CRLF in a CRLF document. */
  readonly eol: string;
  readonly header: TableRow;
  readonly delimiter: TableRow;
  readonly body: readonly TableRow[];
  /** Header, delimiter and every body row — every line a column runs through. */
  readonly rows: readonly TableRow[];
}

function tableGeometry(source: string, block: TableBlock): TableGeometry {
  if (
    block.start < 0 ||
    block.end > source.length ||
    block.end <= block.start
  ) {
    throw new RangeError(
      `Table span [${block.start}, ${block.end}) is not inside the document [0, ${source.length})`,
    );
  }
  // Block boundaries are line boundaries, so splitting the footprint alone is
  // exact and costs the table's own length rather than the document's.
  const lines = splitLines(source.slice(block.start, block.end)).map(
    (line): Line => ({
      start: line.start + block.start,
      contentEnd: line.contentEnd + block.start,
      end: line.end + block.start,
      text: line.text,
    }),
  );
  // The one cheap check that catches a block built from a different string: the
  // second line must still read as a delimiter row of the header's width. Every
  // span below is computed from these lines, so a stale block that got this far
  // would splice into arbitrary offsets and corrupt the document silently.
  const alignments =
    lines.length > 1 ? readDelimiterRow(source, lines[1]) : null;
  if (
    alignments === null ||
    alignments.length !== block.headers.length ||
    lines.length < 2 + block.rows.length
  ) {
    throw new RangeError(STALE_TABLE);
  }

  const toRow = (line: Line): TableRow => ({
    line,
    slots: rowSlots(source, line),
  });
  const header = toRow(lines[0]);
  const delimiter = toRow(lines[1]);
  const body = lines.slice(2, 2 + block.rows.length).map(toRow);
  return {
    // The header line is always terminated — the delimiter row sits under it —
    // so this is never empty and there is no ending to guess at.
    eol: source.slice(lines[0].contentEnd, lines[0].end),
    header,
    delimiter,
    body,
    rows: [header, delimiter, ...body],
  };
}

/** Leading spaces and tabs, so an inserted row lines up with the one above it. */
function leadingSpace(text: string): string {
  let width = 0;
  while (width < text.length && isSpaceChar(text[width])) width += 1;
  return text.slice(0, width);
}

// --- rows -------------------------------------------------------------------

/**
 * Appends an empty row to a table: one zero-width insertion after the last row.
 *
 * The new row carries the HEADER's column count, not the last row's — a table
 * with a ragged row still has the number of columns its header declares, and an
 * inserted row that copied the rag would make the table's shape depend on where
 * you happened to add to it.
 *
 * It is written in the canonical piped form (`|  |  |`) whatever style the rows
 * above use, because that is the only form an empty row HAS: a pipe-less row of
 * empty cells trims to a bare `|`, which is not a row at all. The two spaces per
 * cell are what make the empty cell's span splice to `| x |` rather than `|x |`
 * (see `readCell`). The row's indentation is copied from the row above it, which
 * is safe by construction — a table line indented four columns would have been
 * read as code, so the copy can never push the new row out of the table.
 */
export function addRowEdits(source: string, block: TableBlock): DocumentEdit[] {
  const table = tableGeometry(source, block);
  const anchor =
    table.body.length > 0
      ? table.body[table.body.length - 1].line
      : table.delimiter.line;
  const row = `${leadingSpace(anchor.text)}|${"  |".repeat(block.headers.length)}`;
  // An unterminated anchor is the document's last line, so the new row goes
  // BELOW it and takes the terminator with it — appending `row + eol` there
  // would end a document that deliberately ends without a newline.
  const terminated = anchor.end > anchor.contentEnd;
  return [
    {
      start: anchor.end,
      end: anchor.end,
      text: terminated ? `${row}${table.eol}` : `${table.eol}${row}`,
    },
  ];
}

/**
 * Deletes one body row: its whole line span, terminator included.
 *
 * Deleting the LAST body row is allowed and leaves a header and a delimiter with
 * no body under them, which is still a table — GFM requires the two rows that
 * declare the columns and nothing more. Deleting the table itself is
 * `deleteBlockEdits`, not this.
 *
 * The one span that is not the row's own line is the last line of a document
 * that ends without a newline: that row has no terminator to take, so removing
 * `[start, end)` would leave the PREVIOUS row's terminator behind and the
 * document would end with a newline it never had. The span reaches back over
 * that terminator instead.
 */
export function deleteRowEdits(
  source: string,
  block: TableBlock,
  rowIndex: number,
): DocumentEdit[] {
  const table = tableGeometry(source, block);
  const row = table.body[rowIndex];
  if (row === undefined) {
    throw new RangeError(
      `Row ${rowIndex} is outside this table's ${block.rows.length} rows`,
    );
  }
  const line = row.line;
  if (line.end > line.contentEnd) {
    return [{ start: line.start, end: line.end, text: "" }];
  }
  const previous =
    rowIndex > 0 ? table.body[rowIndex - 1].line : table.delimiter.line;
  return [{ start: previous.contentEnd, end: line.end, text: "" }];
}

// --- columns ----------------------------------------------------------------

/**
 * The insertion that gives one row a cell at its own end.
 *
 * A row with a trailing outer pipe grows by `  |`. A row without one grows by
 * ` |  |` — it GAINS a trailing pipe, because a trailing empty cell cannot be
 * written without it: any whitespace after the last pipe is trimmed away, and
 * the pipe then reads as the optional outer one, leaving the cell count where it
 * started.
 *
 * A row with no slots at all (a bare `|`, which the scanner reports as a row of
 * zero cells) has no end to append to and is skipped rather than invented into a
 * one-cell row.
 */
function appendCellEdit(
  source: string,
  row: TableRow,
  cell: string,
): DocumentEdit[] {
  if (row.slots.length === 0) return [];
  const last = row.slots[row.slots.length - 1];
  // `last.end` is the row's trimmed content end, so the only character that can
  // sit at it is the trailing outer pipe — everything else was trimmed off.
  const trailingPipe = source[last.end] === "|";
  const at = trailingPipe ? last.end + 1 : last.end;
  return [
    { start: at, end: at, text: `${trailingPipe ? "" : " |"} ${cell} |` },
  ];
}

/**
 * Adds a column: one insertion in the header, one in the DELIMITER row, and one
 * in every body row — a single batch, because a header that gained a column
 * while its delimiter did not is no longer a table at all.
 *
 * The new delimiter cell is copied from the last existing one, byte for byte.
 * That is what keeps the marker consistent with its neighbours: it inherits
 * their dash width and their alignment colons (`:--`, `:-:`, `--:`) instead of
 * introducing a third style into a row that had one. The consequence worth
 * naming is that a table whose last column is right-aligned gains a right-
 * aligned column; the alternative — always writing `---` — silently restyles the
 * row it is joining.
 *
 * **Ragged rows.** Every row gains exactly one cell AT ITS OWN END, whether it
 * is short, exact or long. No row's existing cells move and no offset is
 * invented: each row's end is a span each row genuinely has. For a short row
 * this also renders correctly — a row shorter than its header is padded with
 * empty cells by the renderer, so the appended empty cell is indistinguishable
 * from that padding.
 */
export function addColumnEdits(
  source: string,
  block: TableBlock,
): DocumentEdit[] {
  const table = tableGeometry(source, block);
  const delimiterCells = splitRowCells(source, table.delimiter.line);
  const marker = delimiterCells[delimiterCells.length - 1].text;
  return [
    ...appendCellEdit(source, table.header, ""),
    ...appendCellEdit(source, table.delimiter, marker),
    ...table.body.flatMap((row) => appendCellEdit(source, row, "")),
  ];
}

/**
 * The span that removes column `index` from one row: the cell AND one adjacent
 * delimiter, so the remaining pipes still bound the remaining cells.
 *
 * It takes the pipe that FOLLOWS the cell, except for the last column, where it
 * takes the one that precedes it — `| A | B | C |` loses `[slot1.start,
 * slot2.start)` for column 1 and `[slot1.end, slot2.end)` for column 2, and both
 * leave a well-formed row.
 *
 * `null` means this row has no span for that column, and there are exactly two
 * such rows:
 *
 *   * a row SHORTER than the column being deleted has no cell there. Nothing is
 *     emitted for it. (The corpus has rows of 3, 2 and 4 cells under a 3-column
 *     header; deleting column 2 leaves the 2-cell row exactly as written.)
 *   * a row with a single cell has no adjacent pipe to take with it. Deleting
 *     its content would leave a blank line, which ENDS the table and splits it
 *     in two, or a bare `|`, which is a row of no cells. Neither is a row, so
 *     the row is left as the user wrote it — it keeps a value under a heading
 *     that moved, which is visible and fixable, where clearing the cell would be
 *     a structural operation quietly erasing data.
 */
function columnSpan(
  slots: readonly RowSlot[],
  index: number,
): { start: number; end: number } | null {
  if (index >= slots.length || slots.length < 2) return null;
  return index < slots.length - 1
    ? { start: slots[index].start, end: slots[index + 1].start }
    : { start: slots[index - 1].end, end: slots[index].end };
}

/** The text one row's line is left with once `span` is cut out of it. */
function lineWithout(
  row: TableRow,
  span: { start: number; end: number },
): string {
  const at = row.line.start;
  return (
    row.line.text.slice(0, span.start - at) + row.line.text.slice(span.end - at)
  );
}

/**
 * Whether a line would still be READ as a row of the table it sits in.
 *
 * Not "is it well formed" — a ragged row is a row, and this asks nothing about
 * cell counts. It asks the one question the scanner asks of every line under a
 * delimiter row, and the scanner's own classifiers answer it so the two cannot
 * drift: a table body ends at a blank line or at a line that starts a block of
 * its own.
 *
 * The line is SYNTHESIZED because the text does not exist yet — it is what the
 * source would hold once the edit lands, and the question has to be asked before
 * the edit rather than discovered after it. Only `text` is read; the offsets are
 * the ones a standalone line has.
 */
function readsAsRow(text: string): boolean {
  const line: Line = {
    start: 0,
    contentEnd: text.length,
    end: text.length,
    text,
  };
  return !isBlankLine(line) && !startsBlock(line);
}

/**
 * The pipe that keeps a row a ROW when the deletion would stop it being one.
 *
 * Outer pipes are optional on EVERY row, and that is what makes this necessary
 * rather than defensive. A table can be written `a | b` over `--- | ---`, so a
 * cut has two ways to leave a line that is no longer a row:
 *
 *   * it takes the character the line STARTS with — column 0 of a row with no
 *     leading pipe — and whatever cell 1 held becomes the head of the line.
 *     `---` is then a thematic break, ` -` is a list, `#` is a heading; each one
 *     ends the table where it stands, and the rows below it become pipe-noise in
 *     a paragraph. A lone `-` meaning "no value" is among the commonest cells a
 *     real table has, so this is not an exotic input.
 *   * it takes the delimiter row's ONLY pipe, wherever that pipe sits. What is
 *     left is `---` under `b`, and `b` over `---` is a SETEXT HEADING rather
 *     than a one-column table. The table does not become malformed, it becomes
 *     prose, and every cell span in it disappears.
 *
 * Both are repaired the same way and for the same reason: a line that begins
 * with `|` cannot be read as anything but a row, and a LEADING outer pipe is
 * dropped by the scanner and by GFM alike — so putting one back costs one
 * character and cannot change the row's cell count. What it must not do is land
 * on a row that already has one, which would open an empty first cell; that
 * cannot happen, because a row with a leading pipe keeps it (the cut starts past
 * it) and therefore always survives both tests.
 *
 * The delimiter row is asked BOTH questions, because it has to pass both. A
 * delimiter row with no unescaped pipe is not a delimiter row — that is
 * `readDelimiterRow`'s own rule — and a delimiter cell may be a single `-`, so
 * `:-: | - | ---` losing its first column leaves ` - | ---`, which keeps a pipe
 * and is a LIST. Either one alone lets a table through that has stopped being
 * one.
 */
function openingPipeEdit(
  row: TableRow,
  span: { start: number; end: number },
  isDelimiter: boolean,
): DocumentEdit | null {
  const rest = lineWithout(row, span);
  const survives =
    readsAsRow(rest) &&
    (!isDelimiter || unescapedPipePositions(rest, 0).length > 0);
  if (survives) return null;
  const at = row.slots[0].start;
  return { start: at, end: at, text: "|" };
}

/**
 * Deletes a column: one deletion per row that has one, header and delimiter
 * included so the two keep agreeing on the column count, plus the opening pipe
 * any row needs to still read as a row (`openingPipeEdit`).
 *
 * The LAST column cannot be deleted. A table with a header and no body rows is
 * still a table; a table with no columns is not markdown at all — there is no
 * delimiter row that declares zero columns. Deleting the table is
 * `deleteBlockEdits`, which is a different intent and says so.
 */
export function deleteColumnEdits(
  source: string,
  block: TableBlock,
  columnIndex: number,
): DocumentEdit[] {
  const table = tableGeometry(source, block);
  const columns = block.headers.length;
  if (
    !Number.isInteger(columnIndex) ||
    columnIndex < 0 ||
    columnIndex >= columns
  ) {
    throw new RangeError(
      `Column ${columnIndex} is outside this table's ${columns} columns`,
    );
  }
  if (columns < 2) {
    throw new RangeError(
      "A table's last column cannot be deleted: a table with no columns is not a table. Delete the block instead.",
    );
  }
  const edits: DocumentEdit[] = [];
  for (const row of table.rows) {
    const span = columnSpan(row.slots, columnIndex);
    if (span === null) continue;
    edits.push({ start: span.start, end: span.end, text: "" });
    // Zero-width, at the row's own opening — the same offset the deletion starts
    // at for column 0, strictly before it otherwise. Either way it sorts ahead of
    // the deletion and shares no byte with it, so the pair cannot overlap.
    const opening = openingPipeEdit(row, span, row === table.delimiter);
    if (opening !== null) edits.push(opening);
  }
  return edits;
}

// --- blocks -----------------------------------------------------------------

/**
 * The end of a block's own content: its footprint minus the blank run it
 * absorbed.
 *
 * This is `textEnd` generalized to every kind — a table has no editable text
 * span, but it has a last row, and a structural edit needs the same boundary for
 * both. Everything between this and `block.end` is the separation to the block
 * below (or, for the last block, the document's trailing whitespace).
 */
export function blockContentEnd(source: string, block: DocumentBlock): number {
  const lines = splitLines(source.slice(block.start, block.end));
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    if (!isBlankLine(lines[index]))
      return lines[index].contentEnd + block.start;
  }
  return block.start;
}

function blockContent(source: string, block: DocumentBlock): string {
  return source.slice(block.start, blockContentEnd(source, block));
}

function trailingRun(source: string, block: DocumentBlock): string {
  return source.slice(blockContentEnd(source, block), block.end);
}

/**
 * Whether a run of trailing whitespace actually SEPARATES two blocks, which
 * takes a blank line — two terminators — not merely the end of a line. One
 * terminator leaves the next block's first line adjacent to this one, where a
 * paragraph absorbs it and a container lazily continues into it.
 */
function separatesBlocks(run: string): boolean {
  return (run.match(LINE_TERMINATORS) ?? []).length >= 2;
}

function documentEol(source: string): string {
  const match = LINE_TERMINATOR.exec(source);
  return match === null ? "\n" : match[0];
}

function finalTerminator(source: string): string {
  const match = FINAL_TERMINATOR.exec(source);
  return match === null ? "" : match[0];
}

/**
 * A separating run reduced to its line terminators, which is the form this
 * module is willing to WRITE.
 *
 * A blank line may carry spaces and tabs — `"\n \n"` separates two blocks
 * exactly as `"\n\n"` does — and a document that has one is a document nobody
 * can see the difference in. Copying such a run into a boundary the user just
 * asked for would propagate that invisible whitespace to a place it was never
 * typed, so what is measured is the SHAPE of the separation (how many lines, in
 * whose terminator) and not the bytes that happened to fill it. Runs that
 * already exist are never touched by this: `slotText` reuses a slot's own run
 * verbatim, and nothing here rewrites a boundary the operation did not create.
 */
function terminatorsOf(run: string): string {
  return (run.match(LINE_TERMINATORS) ?? []).join("");
}

/**
 * The blank-line separation this document uses between blocks: the most common
 * separating run among its existing boundaries, first-seen winning a tie.
 *
 * Measured rather than assumed, so a CRLF document is separated with CRLF and a
 * document that spaces its blocks two lines apart keeps doing so. The fallback —
 * one blank line in the document's own ending — is for a document with no
 * boundary to learn from, which is any document of one block or none.
 */
function documentSeparation(
  source: string,
  blocks: readonly DocumentBlock[],
): string {
  const counts = new Map<string, number>();
  for (let index = 0; index + 1 < blocks.length; index += 1) {
    const run = trailingRun(source, blocks[index]);
    // Counted in the form it would be written in, so a document that spaces its
    // blocks with a space-bearing blank line and a bare one is measured as
    // having one style rather than two.
    if (separatesBlocks(run)) {
      const shape = terminatorsOf(run);
      counts.set(shape, (counts.get(shape) ?? 0) + 1);
    }
  }
  let best: string | null = null;
  let bestCount = 0;
  for (const [run, count] of counts) {
    if (count > bestCount) {
      best = run;
      bestCount = count;
    }
  }
  const eol = documentEol(source);
  return best ?? `${eol}${eol}`;
}

/**
 * The blocks must cover this document, which is the property the whole model
 * rests on and the one thing that proves the caller's array was parsed from the
 * string it is about to edit. A stale array's offsets are meaningless, and an
 * edit built from them lands in the middle of a sentence.
 */
function assertCovers(source: string, blocks: readonly DocumentBlock[]): void {
  let cursor = 0;
  for (const block of blocks) {
    if (block.start !== cursor || block.end <= block.start) {
      throw new RangeError(STALE_BLOCKS);
    }
    cursor = block.end;
  }
  if (cursor !== source.length) throw new RangeError(STALE_BLOCKS);
}

/**
 * A BOUNDARY may be one past the last block — that is what "append" means — so
 * `blocks.length` is in range here and is not for `assertBlockIndex`.
 */
function assertBoundary(index: number, blocks: readonly unknown[]): void {
  if (!Number.isInteger(index) || index < 0 || index > blocks.length) {
    throw new RangeError(
      `Boundary ${index} is outside this document's [0, ${blocks.length}] block boundaries`,
    );
  }
}

/**
 * An empty document has no block index at all, which is why this is a separate
 * rule rather than a boundary check with the limit moved down by one.
 */
function assertBlockIndex(index: number, blocks: readonly unknown[]): void {
  if (!Number.isInteger(index) || index < 0 || index >= blocks.length) {
    throw new RangeError(
      `Block ${index} is outside this document's ${blocks.length} blocks`,
    );
  }
}

/**
 * Inserts a new block at a block boundary: one zero-width insertion carrying the
 * text and the blank lines that keep it a block of its own.
 *
 * `index` names the boundary — `0` puts the block at the top, `blocks.length`
 * appends it, and any other value puts it immediately before that block. `text`
 * is written VERBATIM and should NOT carry a trailing terminator; the separation
 * is this function's to add and the document's to decide (`documentSeparation`).
 *
 * Blank text is refused rather than inserted. There is no such thing as an empty
 * block: blank lines are absorbed by the block above them, so inserting one adds
 * whitespace and no block at all. A UI wanting an empty heading inserts `"## "`,
 * which really is a block — one with an empty editable span.
 *
 * Appending anchors on the last block's CONTENT rather than its footprint end,
 * so whatever the document already ends with — trailing blank lines, or no final
 * newline at all — stays at the end and the document's shape is unchanged. The
 * degenerate case falls out of the same rule: a document of nothing but
 * whitespace has no content to append after, so its one block's content end is
 * the top of the file and the new block lands there, ahead of the padding rather
 * than after it, which is the only placement that keeps the final newline.
 *
 * One thing it does not promise: that every arrangement means what its parts
 * meant apart. Markdown has constructs that absorb what follows them regardless
 * of blank lines — a list continues into an indented block, an UNCLOSED fence
 * runs to the end of the document and swallows anything appended after it. The
 * separation guarantee makes a block stand alone where a blank line can; where
 * the language says otherwise, the language wins.
 */
export function addBlockEdits(
  source: string,
  blocks: readonly DocumentBlock[],
  index: number,
  text: string,
): DocumentEdit[] {
  assertCovers(source, blocks);
  assertBoundary(index, blocks);
  if (text.trim().length === 0) {
    throw new RangeError(
      "A new block needs text: blank lines are absorbed by the block above them, so an empty block is whitespace and no block at all.",
    );
  }
  const separation = documentSeparation(source, blocks);

  if (index === blocks.length) {
    const at =
      blocks.length === 0
        ? source.length
        : blockContentEnd(source, blocks[blocks.length - 1]);
    // Nothing above to separate from when everything before the anchor is blank,
    // which is the empty document and the whitespace-only one.
    const above = source.slice(0, at).trim().length === 0 ? "" : separation;
    return [{ start: at, end: at, text: `${above}${text}` }];
  }

  const at = blocks[index].start;
  const above =
    index > 0 && !separatesBlocks(trailingRun(source, blocks[index - 1]))
      ? separation
      : "";
  return [{ start: at, end: at, text: `${above}${text}${separation}` }];
}

/**
 * Deletes one block: its FOOTPRINT span, which is exactly right here and would
 * be wrong for an edit — the footprint carries the blank run under the block, so
 * replacing it with a new value would weld two blocks together, while removing
 * it takes the block and the separation it no longer needs.
 *
 * Two spans are not the plain footprint:
 *
 *   * deleting a MIDDLE block whose predecessor does not end in a blank line
 *     writes one terminator back, so the two blocks the deletion introduced to
 *     each other keep the blank line that stops them merging;
 *   * deleting the LAST block reaches back over the blank run above it — that
 *     run separated it from a block that is now the end of the document — and
 *     writes back the document's own final terminator, so a document that ended
 *     with a newline still does and one that ended without one still does not.
 *
 * Deleting the ONLY block empties the document. That is a real outcome rather
 * than an error: the result is `""`, which parses to no blocks, and
 * `addBlockEdits` starts a document from there.
 */
export function deleteBlockEdits(
  source: string,
  blocks: readonly DocumentBlock[],
  index: number,
): DocumentEdit[] {
  assertCovers(source, blocks);
  assertBlockIndex(index, blocks);
  const block = blocks[index];

  if (index === blocks.length - 1) {
    if (index === 0) return [{ start: 0, end: source.length, text: "" }];
    const at = blockContentEnd(source, blocks[index - 1]);
    return [{ start: at, end: source.length, text: finalTerminator(source) }];
  }

  const joins =
    index > 0 && !separatesBlocks(trailingRun(source, blocks[index - 1]));
  return [
    {
      start: block.start,
      end: block.end,
      text: joins ? documentEol(source) : "",
    },
  ];
}

/**
 * The text a slot receives when a block moves into it: the incoming CONTENT,
 * plus whatever separation the slot needs on each side.
 *
 * The slot's own trailing run is reused when it already separates blocks, so a
 * reorder does not restyle a document's spacing. It is replaced by the
 * document's separation when it does not, because the block that used to sit
 * here may have been one a single newline was enough for (a heading interrupts
 * a paragraph; a paragraph does not) and the block arriving may not be.
 *
 * The LAST slot keeps its run whatever it is: that run is the end of the
 * document, not a separation, and padding it would add a trailing blank line.
 */
function slotText(
  source: string,
  blocks: readonly DocumentBlock[],
  index: number,
  content: string,
  separation: string,
): string {
  const block = blocks[index];
  const run = trailingRun(source, block);
  const above =
    index > 0 && !separatesBlocks(trailingRun(source, blocks[index - 1]))
      ? separation
      : "";
  const below =
    index === blocks.length - 1 || separatesBlocks(run) ? run : separation;
  return `${above}${content}${below}`;
}

/**
 * Exchanges two blocks: two footprint-span replacements, each slot keeping its
 * position and separation while its CONTENT trades places with the other's.
 *
 * Exchanging the footprints themselves — content and trailing run together —
 * would move each block's spacing with it, and a block whose run was a bare
 * newline lands above a paragraph and merges into it. Moving only the content is
 * what makes "move this block up" a reorder rather than a reflow.
 *
 * A move up or down is `swapBlocksEdits(source, blocks, index, index ± 1)`. A
 * block swapped with itself is the identity and returns no edits, which is also
 * the only way this could have produced two edits over one span.
 *
 * The one shape it cannot preserve is a document that ends without a newline
 * when the LAST block trades with a content-less one — the leading run of blank
 * lines that `parseBlocks` reports as a block. The last slot ends up empty, so
 * the document ends where the block above it ends, with that block's terminator.
 * Every byte of content is still there and still in the order asked for; what
 * moved is trailing whitespace that only the last block was carrying.
 */
export function swapBlocksEdits(
  source: string,
  blocks: readonly DocumentBlock[],
  first: number,
  second: number,
): DocumentEdit[] {
  assertCovers(source, blocks);
  assertBlockIndex(first, blocks);
  assertBlockIndex(second, blocks);
  if (first === second) return [];

  const separation = documentSeparation(source, blocks);
  const firstContent = blockContent(source, blocks[first]);
  const secondContent = blockContent(source, blocks[second]);
  return [
    {
      start: blocks[first].start,
      end: blocks[first].end,
      text: slotText(source, blocks, first, secondContent, separation),
    },
    {
      start: blocks[second].start,
      end: blocks[second].end,
      text: slotText(source, blocks, second, firstContent, separation),
    },
  ];
}
