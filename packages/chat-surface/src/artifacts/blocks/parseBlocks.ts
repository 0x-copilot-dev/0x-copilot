// The block scanner: one markdown string -> a contiguous list of blocks.
//
// Read `blockModel.ts` first for the contract. The single property this file
// exists to hold is total, non-overlapping coverage:
//
//     parseBlocks(s).map((b) => s.slice(b.start, b.end)).join("") === s
//
// Two structural decisions carry it.
//
// 1. **Every block absorbs its own trailing blank lines.** The alternative —
//    emitting whitespace as its own block — doubles the block count of an
//    ordinary document and makes every renderer filter. Absorption keeps the
//    list equal to what a reader would call "the blocks", at the cost of one
//    rule to remember: `[start, end)` is the block's FOOTPRINT, and the editable
//    span is `textStart`/`textEnd`. Leading whitespace at the top of a document
//    has no block above it to absorb it, so it becomes a `raw` block.
//
// 2. **Anything not modelled becomes `raw`, never a guess.** Lists, blockquotes,
//    HTML and fences are consumed as opaque runs. A fence is consumed BEFORE any
//    table test can see inside it, which is why a code block full of pipe
//    characters cannot be mistaken for a table.
//
// Every classification rule was measured against the renderer these blocks sit
// under — `remark-gfm`, reached through Streamdown — rather than read off the
// CommonMark spec. Two of them are not what the spec reading suggests, and both
// were found by that comparison rather than by inspection: a table interrupts an
// ordinary paragraph but NOT a container's lazy continuation line, and there is
// no front matter, because no front-matter plugin is loaded.

import type {
  ColumnAlignment,
  DocumentBlock,
  RawBlock,
  RawBlockReason,
  TableBlock,
  TableCell,
} from "./blockModel";
import {
  indentWidth,
  interruptsParagraph,
  isBlankLine,
  isBlockquote,
  isFence,
  isHtmlBlockStart,
  isIndentedCode,
  isListItem,
  isSetextUnderline,
  isThematicBreak,
  readAtxHeading,
  splitLines,
  startsBlock,
  type Line,
} from "./lines";
import { readDelimiterRow, splitRowCells } from "./tables";

const OPEN_FENCE = /^ {0,3}(`{3,}|~{3,})/;
const HTML_COMMENT_OPEN = /^ {0,3}<!--/;
const HTML_RAW_TEXT_OPEN = /^ {0,3}<(script|pre|style|textarea)\b/i;

/**
 * Parses a markdown document into blocks that between them cover every byte.
 *
 * Pure: no React, no ports, no substrate primitives. One linear pass over the
 * lines with no backtracking, so it is cheap enough to re-run after every edit
 * instead of maintaining a mutable tree.
 */
export function parseBlocks(source: string): DocumentBlock[] {
  const lines = splitLines(source);
  const blocks: DocumentBlock[] = [];
  let index = 0;

  while (index < lines.length) {
    if (isBlankLine(lines[index])) {
      // Only reachable at the top of the document: every other block absorbs
      // the blank run beneath it.
      const from = index;
      index = skipBlank(lines, index);
      // No content line at all, hence `contentEnd === from`: the block covers
      // the run and its editable span is empty.
      blocks.push(rawBlock(source, lines, from, from, index, "blank"));
      continue;
    }
    const scanned = scanBlock(source, lines, index);
    blocks.push(scanned.block);
    index = scanned.nextLine;
  }

  return blocks;
}

interface Scanned {
  readonly block: DocumentBlock;
  readonly nextLine: number;
}

function scanBlock(source: string, lines: Line[], from: number): Scanned {
  const line = lines[from];

  if (isIndentedCode(line)) {
    const end = readIndentedCodeEnd(lines, from);
    return raw(source, lines, from, end, "indented-code");
  }
  if (isFence(line)) {
    const end = readFencedCodeEnd(lines, from);
    return raw(source, lines, from, end, "fenced-code");
  }
  if (isHtmlBlockStart(line)) {
    return raw(source, lines, from, readHtmlEnd(lines, from), "html");
  }
  if (isBlockquote(line)) {
    const end = readBlockquoteEnd(lines, from);
    return raw(source, lines, from, end, "blockquote");
  }
  if (isThematicBreak(line)) {
    return raw(source, lines, from, from + 1, "thematic-break");
  }
  if (isListItem(line)) {
    const end = readListEnd(lines, from);
    return raw(source, lines, from, end, "list");
  }

  const heading = readAtxHeading(source, line);
  if (heading !== null) {
    const nextLine = skipBlank(lines, from + 1);
    return {
      block: {
        kind: "heading",
        start: line.start,
        end: lines[nextLine - 1].end,
        level: heading.level,
        textStart: heading.textStart,
        textEnd: heading.textEnd,
        text: source.slice(heading.textStart, heading.textEnd),
        separators: heading.separators,
      },
      nextLine,
    };
  }

  const table = readTable(source, lines, from);
  if (table !== null) return table;

  return readParagraph(source, lines, from);
}

// --- raw runs ---------------------------------------------------------------

function raw(
  source: string,
  lines: Line[],
  from: number,
  contentEnd: number,
  reason: RawBlockReason,
): Scanned {
  const nextLine = skipBlank(lines, contentEnd);
  return {
    block: rawBlock(source, lines, from, contentEnd, nextLine, reason),
    nextLine,
  };
}

/**
 * `[from, contentEnd)` are the block's own lines; `[contentEnd, nextLine)` is
 * the blank run it absorbs. The footprint spans both, the editable text spans
 * only the first — the same separation a paragraph draws, for the same reason:
 * an edit that reached into the blank run would weld this block to the next.
 */
function rawBlock(
  source: string,
  lines: Line[],
  from: number,
  contentEnd: number,
  nextLine: number,
  reason: RawBlockReason,
): RawBlock {
  const start = lines[from].start;
  const textEnd = contentEnd > from ? lines[contentEnd - 1].contentEnd : start;
  return {
    kind: "raw",
    start,
    end: lines[nextLine - 1].end,
    reason,
    textStart: start,
    textEnd,
    text: source.slice(start, textEnd),
  };
}

function skipBlank(lines: Line[], from: number): number {
  let index = from;
  while (index < lines.length && isBlankLine(lines[index])) index += 1;
  return index;
}

function readUntilBlank(lines: Line[], from: number): number {
  let index = from;
  while (index < lines.length && !isBlankLine(lines[index])) index += 1;
  return index;
}

function readIndentedCodeEnd(lines: Line[], from: number): number {
  let index = from;
  for (;;) {
    while (
      index < lines.length &&
      !isBlankLine(lines[index]) &&
      isIndentedCode(lines[index])
    ) {
      index += 1;
    }
    // Blank lines inside an indented code block belong to it, but only when
    // indented code resumes underneath; otherwise they are the separator and
    // the absorb step takes them.
    const afterBlank = skipBlank(lines, index);
    if (
      afterBlank > index &&
      afterBlank < lines.length &&
      isIndentedCode(lines[afterBlank])
    ) {
      index = afterBlank;
      continue;
    }
    return index;
  }
}

function readFencedCodeEnd(lines: Line[], from: number): number {
  const open = OPEN_FENCE.exec(lines[from].text);
  if (open === null) return from + 1;
  const marker = open[1][0];
  const width = open[1].length;
  for (let index = from + 1; index < lines.length; index += 1) {
    const close = OPEN_FENCE.exec(lines[index].text);
    if (
      close !== null &&
      close[1][0] === marker &&
      close[1].length >= width &&
      lines[index].text.slice(close[0].length).trim().length === 0
    ) {
      return index + 1;
    }
  }
  // An unclosed fence runs to the end of the document, as CommonMark says.
  return lines.length;
}

function readHtmlEnd(lines: Line[], from: number): number {
  const text = lines[from].text;
  if (HTML_COMMENT_OPEN.test(text)) return readUntilMatch(lines, from, "-->");
  const rawText = HTML_RAW_TEXT_OPEN.exec(text);
  if (rawText !== null) {
    return readUntilMatch(lines, from, `</${rawText[1].toLowerCase()}>`);
  }
  return readUntilBlank(lines, from);
}

function readUntilMatch(lines: Line[], from: number, needle: string): number {
  for (let index = from; index < lines.length; index += 1) {
    if (lines[index].text.toLowerCase().includes(needle)) return index + 1;
  }
  return lines.length;
}

/**
 * Whether an UNMARKED line continues the container above it.
 *
 * This is CommonMark's lazy continuation, and the rule is narrower than
 * "anything up to the next blank line": what the line continues is a PARAGRAPH
 * inside the container, so exactly the things that end a paragraph end the
 * container too. Consuming past them is how a `# Heading` written directly under
 * `- item` (or under `> quoted`) disappears into a raw block and stops being
 * editable, while the renderer shows it as a heading.
 *
 * A table is the exception, and it goes the other way from the top level: a
 * delimiter row DOES interrupt an ordinary paragraph but does NOT interrupt a
 * lazy continuation line, so `- a` followed straight by `| A | B |` is one list
 * and no table. That asymmetry is a container/flow ordering fact about the
 * renderer, not something to reason out from the spec — it was measured against
 * remark-gfm, as was every other rule here.
 */
function continuesLazily(lines: Line[], at: number): boolean {
  return !interruptsParagraph(lines[at]);
}

/**
 * A list runs until a blank line that is NOT followed by more list content — an
 * item's own paragraph, a nested item, or a loose list's next item — or until a
 * line that starts a block of its own.
 */
function readListEnd(lines: Line[], from: number): number {
  let index = from;
  for (;;) {
    // `index === from` is load-bearing, not defensive: it guarantees the first
    // line is consumed, so every path through this loop advances and the block
    // can never be empty. An empty block would put `end` before `start` and
    // break the coverage property the whole model rests on.
    while (
      index < lines.length &&
      !isBlankLine(lines[index]) &&
      (index === from ||
        isListItem(lines[index]) ||
        indentWidth(lines[index].text) >= 2 ||
        continuesLazily(lines, index))
    ) {
      index += 1;
    }
    if (index < lines.length && !isBlankLine(lines[index])) return index;
    const afterBlank = skipBlank(lines, index);
    if (
      afterBlank > index &&
      afterBlank < lines.length &&
      (isListItem(lines[afterBlank]) ||
        indentWidth(lines[afterBlank].text) >= 2)
    ) {
      index = afterBlank;
      continue;
    }
    return index;
  }
}

/** A blockquote runs to the first blank line, or to the first line that starts a block. */
function readBlockquoteEnd(lines: Line[], from: number): number {
  let index = from + 1;
  while (
    index < lines.length &&
    !isBlankLine(lines[index]) &&
    (isBlockquote(lines[index]) || continuesLazily(lines, index))
  ) {
    index += 1;
  }
  return index;
}

// --- table ------------------------------------------------------------------

function readTable(
  source: string,
  lines: Line[],
  from: number,
): Scanned | null {
  const header = readTableHead(source, lines, from);
  if (header === null) return null;

  const rows: TableCell[][] = [];
  let index = from + 2;
  while (
    index < lines.length &&
    !isBlankLine(lines[index]) &&
    !startsBlock(lines[index])
  ) {
    rows.push(splitRowCells(source, lines[index]));
    index += 1;
  }

  const nextLine = skipBlank(lines, index);
  const block: TableBlock = {
    kind: "table",
    start: lines[from].start,
    end: lines[nextLine - 1].end,
    headers: header.cells.map((cell) => cell.text),
    headerCells: header.cells,
    rows,
    alignments: header.alignments,
  };
  return { block, nextLine };
}

/**
 * A header row is a header only because a delimiter row sits under it, and the
 * two must agree on the column count — the rule that keeps `a` over `---` a
 * setext heading while `a` over `| --- |` is a one-column table.
 */
function readTableHead(
  source: string,
  lines: Line[],
  from: number,
): { cells: TableCell[]; alignments: ColumnAlignment[] } | null {
  if (from + 1 >= lines.length) return null;
  if (isIndentedCode(lines[from])) return null;
  const alignments = readDelimiterRow(source, lines[from + 1]);
  if (alignments === null) return null;
  const cells = splitRowCells(source, lines[from]);
  if (cells.length === 0 || cells.length !== alignments.length) return null;
  return { cells, alignments };
}

// --- paragraph --------------------------------------------------------------

function readParagraph(source: string, lines: Line[], from: number): Scanned {
  let index = from + 1;
  let setext = false;
  while (index < lines.length && !isBlankLine(lines[index])) {
    if (isSetextUnderline(lines[index])) {
      // A setext heading. It outranks the thematic break `---` would otherwise
      // be, and it routes whole to `raw` rather than being modelled: its text
      // and its underline are two lines that have to stay consistent, which an
      // in-place edit of one span cannot promise.
      index += 1;
      setext = true;
      break;
    }
    if (interruptsParagraph(lines[index])) break;
    // A table is the one construct that may interrupt a paragraph, matching
    // remark-gfm (verified against the renderer, not assumed).
    if (readTableHead(source, lines, index) !== null) break;
    index += 1;
  }

  if (setext) return raw(source, lines, from, index, "setext-heading");

  const nextLine = skipBlank(lines, index);
  const textStart = lines[from].start;
  const textEnd = lines[index - 1].contentEnd;
  return {
    block: {
      kind: "paragraph",
      start: textStart,
      end: lines[nextLine - 1].end,
      textStart,
      textEnd,
      text: source.slice(textStart, textEnd),
    },
    nextLine,
  };
}
