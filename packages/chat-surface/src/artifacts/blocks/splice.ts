// The splice engine: an edit is an exact span replacement, and nothing else.
//
// SPLICE, NEVER REGENERATE. A document carries prose around its table ("All five
// issues are currently in Cool status"), links inside cells, and constructs the
// block model does not attempt to understand. Parsing the whole document and
// re-emitting it WILL reformat or drop one of them. Replacing the exact character
// span of the edited cell cannot: every byte outside that span is copied through
// untouched, by construction rather than by care.
//
// The one thing to know about batching: every offset in a batch indexes the
// ORIGINAL source. So `applyEdits` never re-indexes anything — it walks the
// source once in ascending order, copying the gaps and substituting the spans.
// That is the same result as applying the edits in descending offset order, with
// no intermediate strings and no chance of an offset going stale between two
// edits of one batch.

import type {
  DocumentEdit,
  EditableBlock,
  TableBlock,
  TableCell,
} from "./blockModel";

/**
 * Applies a batch of span replacements to `source`.
 *
 * The batch may arrive in any order — it is sorted here, and the caller's array
 * is never mutated. Overlapping edits THROW rather than resolving by some
 * last-write-wins rule: two edits over one span is a bug in the caller's delta
 * bookkeeping, and silently dropping one of them would lose a user's typing.
 *
 * Returns `source` itself when there is nothing to apply, so the empty batch is
 * identity in the strictest sense.
 */
export function applyEdits(
  source: string,
  edits: readonly DocumentEdit[],
): string {
  if (edits.length === 0) return source;

  const ordered = [...edits].sort((a, b) => a.start - b.start || a.end - b.end);
  for (let index = 0; index < ordered.length; index += 1) {
    const edit = ordered[index];
    if (
      !Number.isInteger(edit.start) ||
      !Number.isInteger(edit.end) ||
      edit.start < 0 ||
      edit.end < edit.start ||
      edit.end > source.length
    ) {
      throw new RangeError(
        `Edit span [${edit.start}, ${edit.end}) is not inside the document [0, ${source.length})`,
      );
    }
    if (index === 0) continue;
    const previous = ordered[index - 1];
    const overlaps =
      edit.start < previous.end ||
      (edit.start === previous.start && edit.end === previous.end);
    if (overlaps) {
      throw new RangeError(
        `Edits [${previous.start}, ${previous.end}) and [${edit.start}, ${edit.end}) overlap; one span may be edited once per batch`,
      );
    }
  }

  const pieces: string[] = [];
  let cursor = 0;
  for (const edit of ordered) {
    pieces.push(source.slice(cursor, edit.start), edit.text);
    cursor = edit.end;
  }
  pieces.push(source.slice(cursor));
  return pieces.join("");
}

function cellAt(
  block: TableBlock,
  rowIndex: number,
  colIndex: number,
): TableCell {
  const row = block.rows[rowIndex];
  if (row === undefined) {
    throw new RangeError(
      `Row ${rowIndex} is outside this table's ${block.rows.length} rows`,
    );
  }
  const cell = row[colIndex];
  if (cell === undefined) {
    // Ragged rows are kept as written, so a row really can be shorter than the
    // header. There is no span to splice into, and inventing one would mean
    // rewriting the row's shape — a regeneration by another name.
    throw new RangeError(
      `Column ${colIndex} is outside row ${rowIndex}, which has ${row.length} cells`,
    );
  }
  return cell;
}

/** The edit that replaces one body cell. Build it, batch it, then `applyEdits`. */
export function cellEdit(
  block: TableBlock,
  rowIndex: number,
  colIndex: number,
  nextText: string,
): DocumentEdit {
  const cell = cellAt(block, rowIndex, colIndex);
  return { start: cell.start, end: cell.end, text: nextText };
}

/** The edit that replaces one header cell (a column name). */
export function headerCellEdit(
  block: TableBlock,
  colIndex: number,
  nextText: string,
): DocumentEdit {
  const cell = block.headerCells[colIndex];
  if (cell === undefined) {
    throw new RangeError(
      `Column ${colIndex} is outside this table's ${block.headerCells.length} columns`,
    );
  }
  return { start: cell.start, end: cell.end, text: nextText };
}

/**
 * The edit that replaces a prose block's text.
 *
 * It targets `textStart`/`textEnd`, never the block's full footprint: `end`
 * includes the blank line that separates this block from the next, and
 * swallowing it would weld two blocks into one.
 */
export function blockEdit(
  block: EditableBlock,
  nextText: string,
): DocumentEdit {
  return { start: block.textStart, end: block.textEnd, text: nextText };
}

/**
 * Replaces exactly one body cell's span. Every other byte is identical.
 *
 * `nextText` is written VERBATIM — this is a splice, not a formatter, so the
 * markdown a cell already holds (a Linear issue link, an escaped pipe) survives
 * a round trip through the UI unchanged. A caller taking free text from a user
 * runs it through `escapeTableCellText` first; a bare `|` would otherwise open a
 * new column.
 */
export function spliceCell(
  source: string,
  block: TableBlock,
  rowIndex: number,
  colIndex: number,
  nextText: string,
): string {
  return applyEdits(source, [cellEdit(block, rowIndex, colIndex, nextText)]);
}

/** Replaces exactly one header cell's span. Same verbatim contract as `spliceCell`. */
export function spliceHeaderCell(
  source: string,
  block: TableBlock,
  colIndex: number,
  nextText: string,
): string {
  return applyEdits(source, [headerCellEdit(block, colIndex, nextText)]);
}

/** Replaces exactly one prose block's text span. Same verbatim contract as `spliceCell`. */
export function spliceBlock(
  source: string,
  block: EditableBlock,
  nextText: string,
): string {
  return applyEdits(source, [blockEdit(block, nextText)]);
}
