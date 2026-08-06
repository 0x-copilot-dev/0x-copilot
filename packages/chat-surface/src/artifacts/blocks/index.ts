// The block model + splice engine for an editable markdown document.
//
// Pure functions only — no React, no ports, no substrate primitives — so both
// hosts and any renderer can build on them. `blockModel.ts` states the contract;
// `parseBlocks.ts` holds it; `splice.ts` is the only way a document changes.
//
// `lines.ts` and `tables.ts` are the scanner's internals and stay off this
// barrel, except for the two cell-escaping helpers a renderer genuinely needs
// when it writes a user's free text back into a table cell.

export type {
  ColumnAlignment,
  DocumentBlock,
  DocumentBlockKind,
  DocumentEdit,
  EditableBlock,
  HeadingBlock,
  HeadingSeparators,
  ParagraphBlock,
  RawBlock,
  RawBlockReason,
  TableBlock,
  TableCell,
} from "./blockModel";
export { parseBlocks } from "./parseBlocks";
export {
  applyEdits,
  blockEdit,
  cellEdit,
  headerCellEdit,
  spliceBlock,
  spliceCell,
  spliceHeaderCell,
} from "./splice";
// The structural half: the operations that change WHICH spans exist rather than
// what one span holds. They return `DocumentEdit[]` like `cellEdit` does, so a
// structural change batches with a text change through the same `applyEdits` and
// there is no second way a document is written.
export {
  addBlockEdits,
  addColumnEdits,
  addRowEdits,
  blockContentEnd,
  deleteBlockEdits,
  deleteColumnEdits,
  deleteRowEdits,
  swapBlocksEdits,
} from "./structure";
export { escapeTableCellText, unescapeTableCellText } from "./tables";
