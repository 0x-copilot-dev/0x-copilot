// Where an edit lands, and what it becomes.
//
// The editable document holds a map of PENDING edits keyed by target; this
// module is the pure half that turns that map into the `DocumentEdit[]` the
// splice engine takes. Nothing here re-emits markdown. Every function either
// reads the CURRENT text of one span or names the span a new value replaces —
// SPLICE, NEVER REGENERATE, restated at the layer that decides which span.
//
// Two escaping rules, and only two, because a cell's value is markdown the user
// typed and the rest of it is theirs to keep:
//
//   * a table cell goes through `escapeTableCellText`, so a typed `|` becomes
//     `\|` instead of opening a new column, and a pasted newline is flattened
//     instead of ending the row;
//   * a heading has its line breaks flattened, because an ATX heading is one
//     line by construction and a newline inside it would end the heading and
//     leave the rest as a stray paragraph.
//
// A paragraph is written back verbatim, and so is a `raw` block — a list's
// items, a fence's code, a quote's lines. A blank line typed into a paragraph
// really does split it in two on the next read, and a `-` deleted from a list
// item really does turn that line into prose; both are legal markdown edits the
// user made, not corruption, and inventing a rule against either would be this
// module editing the user's document.

import {
  blockEdit,
  cellEdit,
  escapeTableCellText,
  headerCellEdit,
  unescapeTableCellText,
  type DocumentBlock,
  type DocumentEdit,
  type EditableBlock,
  type TableBlock,
} from "@0x-copilot/chat-surface";

/** One editable span, addressed by its position in the parsed block list. */
export type EditTarget =
  | {
      readonly kind: "cell";
      readonly block: number;
      readonly row: number;
      readonly column: number;
    }
  | { readonly kind: "header"; readonly block: number; readonly column: number }
  | { readonly kind: "prose"; readonly block: number };

/** A user's typed value for one target, held locally until Save. */
export interface PendingEdit {
  readonly target: EditTarget;
  readonly value: string;
}

/** The batch, keyed by `targetKey` so re-editing one span replaces it. */
export type PendingEdits = Readonly<Record<string, PendingEdit>>;

/**
 * A stable identity for one span.
 *
 * The keying is what makes the batch safe to hand to `applyEdits`: two edits of
 * one span collapse into one entry here, so the batch can never contain the
 * overlapping pair `applyEdits` throws on. Distinct targets are distinct spans
 * by construction — different blocks never overlap, and two cells of one table
 * never do either.
 */
export function targetKey(target: EditTarget): string {
  switch (target.kind) {
    case "cell":
      return `c:${target.block}:${target.row}:${target.column}`;
    case "header":
      return `h:${target.block}:${target.column}`;
    case "prose":
      return `p:${target.block}`;
  }
}

const LINE_BREAK_RUN = /(?:\r\n|\n|\r)+/g;

/**
 * The block a `prose` target addresses, or `null` when it addresses none.
 *
 * `prose` is the target kind for every block whose editable span is ONE text
 * span: a heading, a paragraph, and a `raw` block alike. That `raw` belongs in
 * that list is the point — the block model declining to UNDERSTAND a list is
 * not it declining to let a user touch one. A raw block owns
 * `[textStart, textEnd)` exactly as a paragraph does and splices through the
 * same primitive; what it does not get is STRUCTURE, which is why a list is
 * edited as its own lines and has no per-item control.
 *
 * A `blank` run is the one exclusion. Its span is empty and its position is
 * BETWEEN two blocks, so writing there would insert text where the document
 * paints nothing to click. The renderer draws no affordance for it; this is the
 * same rule stated on the side a stale target could otherwise reach.
 */
function editableProse(block: DocumentBlock): EditableBlock | null {
  switch (block.kind) {
    case "heading":
    case "paragraph":
      return block;
    case "raw":
      return block.reason === "blank" ? null : block;
    case "table":
      // A table's editable spans are its cells; there is no whole-table span.
      return null;
  }
}

/**
 * The span's value as a user should see and type it: raw markdown, except that
 * a cell's `\|` is shown as the `|` it stands for. Returns `null` when the
 * target does not address an editable span in these blocks.
 */
export function originalValue(
  blocks: readonly DocumentBlock[],
  target: EditTarget,
): string | null {
  const block = blocks[target.block];
  if (block === undefined) return null;
  if (target.kind === "prose") return editableProse(block)?.text ?? null;
  if (block.kind !== "table") return null;
  const cell =
    target.kind === "header"
      ? block.headerCells[target.column]
      : block.rows[target.row]?.[target.column];
  return cell === undefined ? null : unescapeTableCellText(cell.text);
}

/**
 * The span replacement one pending edit becomes.
 *
 * Throws rather than skipping when the target does not resolve. A target that
 * has gone stale means the caller's bookkeeping disagrees with the document,
 * and quietly dropping it would silently discard something a user typed —
 * the one failure mode this whole path exists to prevent.
 */
export function documentEditFor(
  blocks: readonly DocumentBlock[],
  target: EditTarget,
  value: string,
): DocumentEdit {
  const block = blocks[target.block];
  if (block === undefined) {
    throw new RangeError(`No block at index ${target.block}`);
  }
  if (target.kind === "prose") {
    const prose = editableProse(block);
    if (prose === null) {
      throw new RangeError(
        `Block ${target.block} is not editable prose (${
          block.kind === "raw" ? `raw ${block.reason}` : block.kind
        })`,
      );
    }
    // A heading is one line by construction. Everything else — a paragraph's
    // prose, a list's items, a fence's code — is written back verbatim, line
    // breaks and all, because there the lines ARE the construct.
    return blockEdit(
      prose,
      prose.kind === "heading" ? value.replace(LINE_BREAK_RUN, " ") : value,
    );
  }
  if (block.kind !== "table") {
    throw new RangeError(`Block ${target.block} is not a table`);
  }
  const text = escapeTableCellText(value);
  return target.kind === "header"
    ? headerCellEdit(block, target.column, text)
    : cellEdit(block, target.row, target.column, text);
}

/** The whole batch, in no particular order — `applyEdits` sorts it itself. */
export function documentEditsFor(
  blocks: readonly DocumentBlock[],
  pending: PendingEdits,
): readonly DocumentEdit[] {
  return Object.values(pending).map((edit) =>
    documentEditFor(blocks, edit.target, edit.value),
  );
}

/**
 * Records a typed value, or forgets it when the user typed the original back.
 *
 * Dropping a no-op edit is not a nicety: it keeps the dirty count honest, so
 * "3 unsaved edits" means three spans really differ, and Save with nothing
 * pending stays impossible.
 */
export function commitEdit(
  pending: PendingEdits,
  blocks: readonly DocumentBlock[],
  target: EditTarget,
  value: string,
): PendingEdits {
  const key = targetKey(target);
  if (value === originalValue(blocks, target)) return withoutKey(pending, key);
  return { ...pending, [key]: { target, value } };
}

/** Restores one target to what it held before an edit session opened. */
export function revertEdit(
  pending: PendingEdits,
  target: EditTarget,
  previous: PendingEdit | undefined,
): PendingEdits {
  const key = targetKey(target);
  return previous === undefined
    ? withoutKey(pending, key)
    : { ...pending, [key]: previous };
}

function withoutKey(pending: PendingEdits, key: string): PendingEdits {
  if (pending[key] === undefined) return pending;
  const { [key]: _removed, ...rest } = pending;
  return rest;
}

/**
 * Every cell of one table in tab order: the header row, then each body row.
 *
 * Built from the rows as written rather than from `columns × rows`, because a
 * RAGGED row keeps its own length in the block model. Indexing arithmetic would
 * land Tab on a cell that has no span to splice into.
 */
function cellSequence(
  block: TableBlock,
  blockIndex: number,
): readonly EditTarget[] {
  const sequence: EditTarget[] = block.headerCells.map((_cell, column) => ({
    kind: "header",
    block: blockIndex,
    column,
  }));
  block.rows.forEach((row, row_) =>
    row.forEach((_cell, column) =>
      sequence.push({ kind: "cell", block: blockIndex, row: row_, column }),
    ),
  );
  return sequence;
}

/**
 * The cell Tab (`step: 1`) or Shift+Tab (`step: -1`) moves to, or `null` at
 * either end of the table. Movement stays inside one table: the next thing
 * after the last cell is not another block's prose, it is nothing.
 */
export function nextCellTarget(
  blocks: readonly DocumentBlock[],
  target: EditTarget,
  step: 1 | -1,
): EditTarget | null {
  if (target.kind === "prose") return null;
  const block = blocks[target.block];
  if (block === undefined || block.kind !== "table") return null;
  const sequence = cellSequence(block, target.block);
  const key = targetKey(target);
  const index = sequence.findIndex((item) => targetKey(item) === key);
  if (index === -1) return null;
  return sequence[index + step] ?? null;
}
