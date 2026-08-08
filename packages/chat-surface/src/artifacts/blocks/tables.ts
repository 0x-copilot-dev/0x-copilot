// GFM pipe-table row scanning: where the cells are, not what they mean.
//
// Every rule here was pinned against the renderer the product actually uses
// (`remark-gfm`, reached through Streamdown in `messages/MarkdownText.tsx`),
// because a block model that disagrees with the renderer puts an editable table
// where the user sees prose, or the reverse:
//
//   * the delimiter row must contain at least one unescaped `|`, and its cell
//     count must equal the header's — `a\n---\n` is a setext heading, while
//     `a\n| --- |\n` really is a one-column table;
//   * outer pipes are optional on every row (`a | b` is a two-cell row);
//   * `\|` is a literal pipe inside a cell, not a delimiter;
//   * a body row keeps its own cell count, ragged or not.

import { indentWidth, isListItem, isSpaceChar, type Line } from "./lines";
import type { ColumnAlignment, TableCell } from "./blockModel";

const DELIMITER_CELL = /^:?-+:?$/;

/**
 * Absolute offsets of the `|` characters that delimit cells.
 *
 * A pipe preceded by an ODD number of backslashes is escaped and belongs to the
 * cell — `| x \| y | z |` is two cells, not three.
 */
export function unescapedPipePositions(text: string, base: number): number[] {
  const positions: number[] = [];
  let backslashes = 0;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (char === "\\") {
      backslashes += 1;
      continue;
    }
    if (char === "|" && backslashes % 2 === 0) positions.push(base + index);
    backslashes = 0;
  }
  return positions;
}

function readCell(
  source: string,
  contentStart: number,
  contentEnd: number,
): TableCell {
  let start = contentStart;
  let end = contentEnd;
  while (start < end && isSpaceChar(source[start])) start += 1;
  while (end > start && isSpaceChar(source[end - 1])) end -= 1;
  if (start === end && contentEnd > contentStart) {
    // An all-whitespace cell: anchor the empty span one character in, so `|  |`
    // splices to `| x |`. Any anchor is byte-safe; this one also looks right.
    start = contentStart + 1;
    end = start;
  }
  return { text: source.slice(start, end), start, end };
}

/**
 * One cell's span BEFORE its padding is trimmed — everything between the two
 * delimiters that bound it.
 *
 * A `TableCell` is what the user edits; a slot is where the row's structure is.
 * The two differ by the padding spaces, and that difference is exactly what a
 * structural edit needs: `slots[i].end` is the offset of the pipe that closes
 * cell `i` (or, for the last slot, the row's trimmed end), so a column can be
 * removed by deleting `[slots[i].start, slots[i + 1].start)` — the cell WITH the
 * delimiter that follows it. Trimmed cell spans cannot express that; they stop
 * short of the pipes on both sides.
 */
export interface RowSlot {
  readonly start: number;
  readonly end: number;
}

/**
 * Splits one table row into slots at its unescaped pipes, dropping the optional
 * outer pipes.
 *
 * Returns an empty array for a row that carries no cell at all (`|` on its own),
 * which is how a caller rejects it as a header or delimiter row.
 *
 * Slots are contiguous with one delimiter between them: `slots[i].end` is the
 * pipe position and `slots[i + 1].start` is one past it. The last slot ends at
 * the row's trimmed content end, which is the character BEFORE a trailing outer
 * pipe when the row has one.
 */
export function rowSlots(source: string, line: Line): RowSlot[] {
  const pipes = unescapedPipePositions(line.text, line.start);
  let contentStart = line.start;
  let contentEnd = line.contentEnd;
  while (contentStart < contentEnd && isSpaceChar(source[contentStart]))
    contentStart += 1;
  while (contentEnd > contentStart && isSpaceChar(source[contentEnd - 1]))
    contentEnd -= 1;
  if (contentStart === contentEnd) return [];

  let firstPipe = 0;
  let cursor = contentStart;
  if (pipes.length > 0 && pipes[0] === contentStart) {
    firstPipe = 1;
    cursor = contentStart + 1;
  }
  let lastPipe = pipes.length;
  let lastCellEnd = contentEnd;
  if (pipes.length > 0 && pipes[pipes.length - 1] === contentEnd - 1) {
    lastPipe = pipes.length - 1;
    lastCellEnd = contentEnd - 1;
  }
  if (lastPipe < firstPipe) return [];

  const slots: RowSlot[] = [];
  for (let index = firstPipe; index < lastPipe; index += 1) {
    slots.push({ start: cursor, end: pipes[index] });
    cursor = pipes[index] + 1;
  }
  slots.push({ start: cursor, end: lastCellEnd });
  return slots;
}

/**
 * Splits one table row into cells: the slots above, with the padding trimmed off
 * each one. Cells and slots are derived from the SAME scan, so a structural edit
 * and a cell edit can never disagree about where a row's columns are.
 */
export function splitRowCells(source: string, line: Line): TableCell[] {
  return rowSlots(source, line).map((slot) =>
    readCell(source, slot.start, slot.end),
  );
}

/**
 * Reads a delimiter row (`| --- | :-: |`) into per-column alignments, or returns
 * `null` when the line is not one — which is the whole table test, since a header
 * row is only a header because the line under it is a delimiter row.
 */
export function readDelimiterRow(
  source: string,
  line: Line,
): ColumnAlignment[] | null {
  if (indentWidth(line.text) >= 4) return null;
  // `-` is both a delimiter character and a bullet, and the bullet wins: `- | ---`
  // opens a LIST to remark-gfm, so `a | b` above it is a paragraph and not a
  // header. Nothing else in `DELIMITER_CELL`'s alphabet can start a list item —
  // `+`/`*` and a digit all fail the cell test — so this one line is the whole
  // rule. Without it the scanner mounts an editable grid over what the user is
  // looking at as prose followed by a bullet.
  if (isListItem(line)) return null;
  if (unescapedPipePositions(line.text, 0).length === 0) return null;
  const cells = splitRowCells(source, line);
  if (cells.length === 0) return null;
  const alignments: ColumnAlignment[] = [];
  for (const cell of cells) {
    if (!DELIMITER_CELL.test(cell.text)) return null;
    const left = cell.text.startsWith(":");
    const right = cell.text.endsWith(":");
    alignments.push(
      left && right ? "center" : left ? "left" : right ? "right" : null,
    );
  }
  return alignments;
}

const LINE_BREAK_RUN = /(?:\r\n|\n|\r)+/g;

/**
 * Escapes a value for use as a table cell's markdown source.
 *
 * `spliceCell` writes VERBATIM — it is a splice, not a formatter — so a caller
 * taking free text from a user runs it through here first. Two structural
 * hazards, and only those two: an unescaped `|` would open a new column, and a
 * line break would end the row. Everything else the user typed is markdown and
 * is left exactly as typed, links and emphasis included.
 *
 * Idempotent: an already-escaped `\|` is copied through untouched, so applying
 * this on every save cannot accumulate backslashes.
 *
 * Note this is NOT streamdown's `escapeMarkdownTableCell`, which also escapes
 * `\` because its input is plain rendered text on the copy-table path. Ours
 * takes the cell's MARKDOWN, so escaping backslashes would turn `\*` into a
 * literal `\*` and break the emphasis the user typed — and it would not be
 * idempotent.
 */
export function escapeTableCellText(text: string): string {
  let escaped = "";
  let index = 0;
  const flattened = text.replace(LINE_BREAK_RUN, " ");
  while (index < flattened.length) {
    const char = flattened[index];
    if (char === "\\" && index + 1 < flattened.length) {
      escaped += char + flattened[index + 1];
      index += 2;
      continue;
    }
    escaped += char === "|" ? "\\|" : char;
    index += 1;
  }
  return escaped;
}

/**
 * The inverse for display: turns `\|` back into `|` and leaves every other
 * markdown escape (`\*`, `\_`, `\\`) alone, since those mean something to the
 * renderer and are not this module's to interpret.
 */
export function unescapeTableCellText(text: string): string {
  let plain = "";
  let index = 0;
  while (index < text.length) {
    const char = text[index];
    if (char === "\\" && index + 1 < text.length) {
      const next = text[index + 1];
      plain += next === "|" ? "|" : char + next;
      index += 2;
      continue;
    }
    plain += char;
    index += 1;
  }
  return plain;
}
