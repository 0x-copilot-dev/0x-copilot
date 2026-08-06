// Line splitting and CommonMark line classification for the block scanner.
//
// The scanner is line-based on purpose. Offsets fall out of it for free and every
// block boundary is a line boundary, which is what makes total coverage
// (`join(slices) === source`) provable rather than hoped for. A mdast round trip
// would give richer structure and NO reliable inter-node spans: blank lines
// between nodes belong to nobody, so the gaps would have to be reconstructed
// anyway — and reconstruction is the thing this whole module exists to avoid.
//
// The classifiers below are CommonMark's block starts, trimmed to what the
// scanner actually branches on. Where a rule is expensive to implement exactly,
// the classifier errs toward `raw` — the catch-all costs a block its in-place
// editing, never a byte of the document.

import type { HeadingBlock } from "./blockModel";

/** One source line: its content span, and its span including the line terminator. */
export interface Line {
  /** Offset of the line's first character. */
  readonly start: number;
  /** Offset of the line terminator (or of EOF for a final unterminated line). */
  readonly contentEnd: number;
  /** Offset one past the line terminator — the next line's `start`. */
  readonly end: number;
  /** `source.slice(start, contentEnd)`. */
  readonly text: string;
}

/**
 * Splits on `\n`, `\r\n` and a lone `\r`, the three terminators CommonMark
 * recognizes. A document with CRLF endings therefore keeps them: the terminator
 * lives between `contentEnd` and `end` and no block boundary ever splits it.
 */
export function splitLines(source: string): Line[] {
  const lines: Line[] = [];
  let cursor = 0;
  while (cursor < source.length) {
    let contentEnd = cursor;
    while (
      contentEnd < source.length &&
      source[contentEnd] !== "\n" &&
      source[contentEnd] !== "\r"
    ) {
      contentEnd += 1;
    }
    let end = contentEnd;
    if (end < source.length) {
      end += source[end] === "\r" && source[end + 1] === "\n" ? 2 : 1;
    }
    lines.push({
      start: cursor,
      contentEnd,
      end,
      text: source.slice(cursor, contentEnd),
    });
    cursor = end;
  }
  return lines;
}

/** Space or tab. Line terminators never appear inside a `Line`'s text. */
export function isSpaceChar(char: string | undefined): boolean {
  return char === " " || char === "\t";
}

export function isBlankLine(line: Line): boolean {
  return line.text.trim().length === 0;
}

/** Leading indentation in columns, with tabs advancing to the next 4-column stop. */
export function indentWidth(text: string): number {
  let width = 0;
  for (const char of text) {
    if (char === " ") width += 1;
    else if (char === "\t") width += 4 - (width % 4);
    else break;
  }
  return width;
}

const ATX_HEADING = /^ {0,3}(#{1,6})(?:[ \t]+|$)/;
const FENCE = /^ {0,3}(?:`{3,}|~{3,})/;
const THEMATIC_BREAK =
  /^ {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$/;
const BLOCKQUOTE = /^ {0,3}>/;
const BULLET_ITEM = /^ {0,3}[-+*](?:[ \t]+\S|[ \t]*$)/;
const ORDERED_ITEM = /^ {0,3}(\d{1,9})[.)](?:[ \t]+\S|[ \t]*$)/;
const HTML_START = /^ {0,3}<[!?/A-Za-z]/;
const SETEXT_UNDERLINE = /^ {0,3}(?:=+|-+)[ \t]*$/;

/** 4+ columns of indentation: an indented code block, and never a table or heading. */
export function isIndentedCode(line: Line): boolean {
  return indentWidth(line.text) >= 4;
}

export function isAtxHeading(line: Line): boolean {
  return !isIndentedCode(line) && ATX_HEADING.test(line.text);
}

export function isFence(line: Line): boolean {
  return !isIndentedCode(line) && FENCE.test(line.text);
}

export function isThematicBreak(line: Line): boolean {
  return !isIndentedCode(line) && THEMATIC_BREAK.test(line.text);
}

export function isBlockquote(line: Line): boolean {
  return !isIndentedCode(line) && BLOCKQUOTE.test(line.text);
}

export function isListItem(line: Line): boolean {
  if (isIndentedCode(line)) return false;
  return BULLET_ITEM.test(line.text) || ORDERED_ITEM.test(line.text);
}

export function isHtmlBlockStart(line: Line): boolean {
  return !isIndentedCode(line) && HTML_START.test(line.text);
}

/** `===` / `---` under a paragraph line: a setext heading, which outranks a thematic break. */
export function isSetextUnderline(line: Line): boolean {
  return !isIndentedCode(line) && SETEXT_UNDERLINE.test(line.text);
}

/** Any line that opens a block, used to end a table body and to start a raw run. */
export function startsBlock(line: Line): boolean {
  return (
    isIndentedCode(line) ||
    isFence(line) ||
    isAtxHeading(line) ||
    isThematicBreak(line) ||
    isBlockquote(line) ||
    isListItem(line) ||
    isHtmlBlockStart(line)
  );
}

/**
 * Whether this line ends the paragraph running above it.
 *
 * Two CommonMark rules are load-bearing here and are implemented exactly:
 * indented code does NOT interrupt (it is a lazy continuation line), and an
 * ordered list interrupts only when it starts at `1`. Two are deliberately
 * loose: an empty list item is treated as an interrupter when it is a bullet
 * (`-` alone is far more often a setext underline, which is checked first), and
 * `HTML_START` covers CommonMark's html types 1-7 with one regex where the spec
 * distinguishes them. Both looser cases can only move a span from `paragraph`
 * into `raw`, which renders the same markdown and costs only in-place editing.
 */
export function interruptsParagraph(line: Line): boolean {
  if (isIndentedCode(line)) return false;
  if (isAtxHeading(line)) return true;
  if (isFence(line)) return true;
  if (isThematicBreak(line)) return true;
  if (isBlockquote(line)) return true;
  if (isHtmlBlockStart(line)) return true;
  if (BULLET_ITEM.test(line.text)) return true;
  const ordered = ORDERED_ITEM.exec(line.text);
  return ordered !== null && Number(ordered[1]) === 1;
}

/** Parses an ATX heading line into its level and the span of its editable text. */
export function readAtxHeading(
  source: string,
  line: Line,
): { level: HeadingBlock["level"]; textStart: number; textEnd: number } | null {
  const match = ATX_HEADING.exec(line.text);
  if (match === null || isIndentedCode(line)) return null;
  // `#{1,6}` bounds the run, so the length is in range by construction.
  const level = match[1].length as HeadingBlock["level"];
  let textStart = line.start + match[0].length;
  let textEnd = line.contentEnd;
  while (textStart < textEnd && isSpaceChar(source[textStart])) textStart += 1;
  while (textEnd > textStart && isSpaceChar(source[textEnd - 1])) textEnd -= 1;
  // A trailing run of `#` closes the heading only when whitespace precedes it,
  // so `# foo#` keeps its hash and `# foo #` does not.
  let closing = textEnd;
  while (closing > textStart && source[closing - 1] === "#") closing -= 1;
  if (closing < textEnd) {
    if (closing === textStart) {
      textEnd = textStart;
    } else if (isSpaceChar(source[closing - 1])) {
      textEnd = closing;
      while (textEnd > textStart && isSpaceChar(source[textEnd - 1]))
        textEnd -= 1;
    }
  }
  return { level, textStart, textEnd };
}
