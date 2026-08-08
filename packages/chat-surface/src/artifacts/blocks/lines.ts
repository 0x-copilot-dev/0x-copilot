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

import type { HeadingBlock, HeadingSeparators } from "./blockModel";

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
const SETEXT_UNDERLINE = /^ {0,3}(?:=+|-+)[ \t]*$/;

/**
 * CommonMark's HTML block type 6 tag names, verbatim.
 *
 * The list is the whole difference between an HTML block that may interrupt a
 * paragraph and one that may not, so it is spelled out rather than approximated
 * by "looks like a tag" — see `isHtmlParagraphInterrupt`.
 */
const HTML_BLOCK_TAGS =
  "address|article|aside|base|basefont|blockquote|body|caption|center|col|" +
  "colgroup|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|" +
  "form|frame|frameset|h1|h2|h3|h4|h5|h6|head|header|hr|html|iframe|legend|li|" +
  "link|main|menu|menuitem|nav|noframes|ol|optgroup|option|p|param|search|" +
  "section|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul";

/**
 * HTML block types 1-6 — every type EXCEPT type 7, which is the generic
 * "one complete tag alone on a line" case.
 *
 * `<script`/`<pre`/`<style`/`<textarea` (1), `<!--` (2), `<?` (3), `<!DOCTYPE`
 * (4), `<![CDATA[` (5), and an open or close tag from `HTML_BLOCK_TAGS` (6).
 */
const HTML_PARAGRAPH_INTERRUPT = new RegExp(
  "^ {0,3}(?:" +
    "<(?:script|pre|style|textarea)(?:[ \\t>]|$)" +
    "|<!--" +
    "|<\\?" +
    "|<!\\[CDATA\\[" +
    "|<![A-Za-z]" +
    `|</?(?:${HTML_BLOCK_TAGS})(?:[ \\t]|/?>|$)` +
    ")",
  "i",
);

/**
 * HTML block type 7: ONE complete open or closing tag, alone on its line.
 *
 * "Alone" is the whole rule and the reason this is spelled out rather than
 * approximated by `<` plus a letter. `<br/>` opens a block; `<span>x</span>`
 * does not, because the tag is followed by text — it is a paragraph carrying
 * inline html, and remark-gfm will happily read a delimiter row under it as a
 * TABLE whose header is that line.
 */
const HTML_TAG_NAME = "[A-Za-z][A-Za-z0-9-]*";
const HTML_ATTRIBUTE =
  "(?:[ \\t]+[A-Za-z_:][A-Za-z0-9_.:-]*" +
  "(?:[ \\t]*=[ \\t]*(?:[^ \\t\"'=<>`]+|'[^']*'|\"[^\"]*\"))?)";
const HTML_TYPE_7 = new RegExp(
  `^ {0,3}(?:<${HTML_TAG_NAME}${HTML_ATTRIBUTE}*[ \\t]*/?>` +
    `|</${HTML_TAG_NAME}[ \\t]*>)[ \\t]*$`,
);

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

const BLOCKQUOTE_MARKER = /^ {0,3}>[ \t]?/;
const LIST_MARKER = /^ {0,3}(?:[-+*]|\d{1,9}[.)])(?:[ \t]+|[ \t]*$)/;
/** Drops up to `columns` columns of leading whitespace, tabs counted as stops. */
function dropColumns(text: string, columns: number): string {
  let width = 0;
  let index = 0;
  while (index < text.length && width < columns) {
    if (text[index] === " ") width += 1;
    else if (text[index] === "\t") width += 4 - (width % 4);
    else break;
    index += 1;
  }
  return text.slice(index);
}

/**
 * The column an item's own content starts at when this line opens a list item
 * `indent` columns in, or `null` when it opens none.
 *
 * The `indent` argument is what makes a NESTED item visible. `    - x` is
 * indented code at the top level, and the item it really is inside `-` (whose
 * content starts at column 2), which is the difference between one list and a
 * list plus a stranded paragraph.
 */
export function listItemContentColumn(line: Line, indent = 0): number | null {
  const own = indentWidth(line.text);
  if (own < indent || own - indent >= 4) return null;
  const text = dropColumns(line.text, own);
  if (!BULLET_ITEM.test(text) && !ORDERED_ITEM.test(text)) return null;
  const marker = LIST_MARKER.exec(text);
  if (marker === null) return null;
  const width = indentWidth(marker[0].replace(/\S/g, " "));
  // An item with nothing after its marker has no space to measure, and its
  // content column is one past the marker.
  const empty = marker[0].trimEnd().length === marker[0].length;
  return own + width + (empty ? 1 : 0);
}

/**
 * The line's own content: past the indentation the container owns, and past any
 * container markers it carries.
 */
function blockContent(text: string, indent: number): string {
  let content = dropColumns(text, indent);
  for (;;) {
    const quote = BLOCKQUOTE_MARKER.exec(content);
    if (quote !== null) {
      content = content.slice(quote[0].length);
      continue;
    }
    const item = LIST_MARKER.exec(content);
    if (item !== null) {
      content = content.slice(item[0].length);
      continue;
    }
    return content;
  }
}

/**
 * Whether this line opens a run that SWALLOWS the lines under it — a fence or an
 * HTML block, both of which run past a heading, a nested bullet and anything
 * else until a blank line closes them.
 *
 * It is the difference between a paragraph that merely ended and a block that is
 * still open, and only the second one blocks the lines below from meaning
 * anything: `- x` / `<br/>` / `    - x` is ONE item whose html block ate the
 * nested bullet, so the delimiter row two lines later starts a table outside the
 * list rather than continuing it.
 */
export function opensSwallowingRun(line: Line, indent = 0): boolean {
  if (isBlankLine(line)) return false;
  const content = blockContent(line.text, indent);
  return (
    FENCE.test(content) ||
    HTML_PARAGRAPH_INTERRUPT.test(content) ||
    HTML_TYPE_7.test(content)
  );
}

/**
 * Whether a paragraph is still OPEN under this line — the precondition for the
 * next unmarked line to continue a container lazily.
 *
 * Lazy continuation continues a paragraph, not a container, so a container whose
 * last line closed one has nothing left to continue and ends instead. `- x` /
 * `   # a` / `x` is a list holding a heading and then a SEPARATE paragraph;
 * consuming that `x` into the list is how a table written two lines further down
 * disappears inside a raw block. `> # h` and an empty `-` close it the same way,
 * never having opened one at all — and `<br/>` closes it while staying inside the
 * item, which is why this is a question about the paragraph and not about the
 * container.
 *
 * Container markers are stripped so the test reads the line's CONTENT (an item's
 * `# a` is a heading exactly as it would be at the top level). Leading SPACES are
 * not: every classifier below already refuses 4 columns of indentation, which is
 * what keeps an indented line inside a quote reading as the prose it is rather
 * than as the heading it resembles.
 */
export function leavesParagraphOpen(line: Line, indent = 0): boolean {
  if (isBlankLine(line)) return false;
  const content = blockContent(line.text, indent);
  if (content.trim().length === 0) return false;
  if (ATX_HEADING.test(content)) return false;
  if (THEMATIC_BREAK.test(content)) return false;
  if (FENCE.test(content)) return false;
  if (HTML_PARAGRAPH_INTERRUPT.test(content)) return false;
  if (HTML_TYPE_7.test(content)) return false;
  return true;
}

export function isHtmlBlockStart(line: Line): boolean {
  if (isIndentedCode(line)) return false;
  return (
    HTML_PARAGRAPH_INTERRUPT.test(line.text) || HTML_TYPE_7.test(line.text)
  );
}

/**
 * Whether an HTML block starting here may interrupt a paragraph — types 1-6 but
 * NOT type 7.
 *
 * This is the one place the distinction is worth the tag list. `<br/>` and
 * `<span>x</span>` are type 7: written under a line of prose they are INLINE
 * html inside that paragraph, and `a\n<br/>\nb` is a single paragraph to
 * remark-gfm. Treating them as a block start there does not merely mislabel one
 * span — the html run then swallows every line to the next blank one, so the
 * `# Heading` written under `<br/>` stops being a heading. Measured against
 * remark-gfm, like every other rule here.
 */
export function isHtmlParagraphInterrupt(line: Line): boolean {
  return !isIndentedCode(line) && HTML_PARAGRAPH_INTERRUPT.test(line.text);
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
 * ordered list interrupts only when it starts at `1`. A third — HTML block type
 * 7 does not interrupt — is exact too, and is the reason `isHtmlBlockStart` is
 * NOT the predicate used here; see `isHtmlParagraphInterrupt`.
 *
 * One rule stays deliberately loose: an empty list item is treated as an
 * interrupter when it is a bullet (`-` alone is far more often a setext
 * underline, which is checked first). That looser case can only move a span from
 * `paragraph` into `raw`, which renders the same markdown and costs only
 * in-place editing.
 */
export function interruptsParagraph(line: Line): boolean {
  if (isIndentedCode(line)) return false;
  if (isAtxHeading(line)) return true;
  if (isFence(line)) return true;
  if (isThematicBreak(line)) return true;
  if (isBlockquote(line)) return true;
  if (isHtmlParagraphInterrupt(line)) return true;
  if (BULLET_ITEM.test(line.text)) return true;
  const ordered = ORDERED_ITEM.exec(line.text);
  return ordered !== null && Number(ordered[1]) === 1;
}

/**
 * Parses an ATX heading line into its level, the span of its editable text, and
 * the separators a replacement must carry.
 *
 * The regex's `$` alternative is what makes the last part necessary: `######`
 * is a heading whose text span is EMPTY and lands immediately after the hash
 * run, with no space in the source to sit inside. See `HeadingSeparators`.
 */
export function readAtxHeading(
  source: string,
  line: Line,
): {
  level: HeadingBlock["level"];
  textStart: number;
  textEnd: number;
  separators: HeadingSeparators;
} | null {
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
  // Both loops above stopped ON a non-space, so the only byte that can abut the
  // span is a marker `#` — every other neighbour is whitespace or the end of the
  // line, and both already separate the text from the marker. A `#` does not,
  // and welding to one is how `######` + `Summary` stops being a heading at all.
  // Non-empty text always has whitespace on both sides (the `$` alternative only
  // matches a hash run with nothing after it, and a closing run is only stripped
  // when a space precedes it), so in practice this only ever fires for a
  // text-less heading — but it is derived from the source rather than assumed.
  return {
    level,
    textStart,
    textEnd,
    separators: {
      before: source[textStart - 1] === "#" ? " " : "",
      after: source[textEnd] === "#" ? " " : "",
    },
  };
}
