// The block model for an editable markdown document.
//
// An artifact body is ONE STRING today, rendered markdown -> HTML. That is why a
// table in it cannot be clicked into: there is no table component, only text that
// looks like a grid. This module gives that string a second reading — a list of
// blocks, each carrying the EXACT `[start, end)` span it occupies in the source —
// so a renderer can mount a real control per block and an edit can be written back
// as a span replacement.
//
// The governing rule (docs/plan/generative-ui-floor/EDITABLE-SURFACE-DESIGN.md):
// **splice, never regenerate**. The string stays authoritative. Nothing here
// re-emits markdown, normalizes whitespace, or re-serializes a parsed tree — a
// document carries prose, links inside cells, and constructs this model does not
// know about, and any of those would be reformatted or dropped by a
// parse-then-print round trip.
//
// The `raw` catch-all is what makes that safe. Anything the scanner declines to
// model (code fences, lists, blockquotes, HTML, front matter, setext headings)
// becomes a `raw` block that owns its span verbatim. Coverage is total and
// non-overlapping, which is the one property everything else rests on:
//
//     parseBlocks(s).map((b) => s.slice(b.start, b.end)).join("") === s
//
// Offsets are UTF-16 code-unit indices — the same unit `String.prototype.slice`
// takes — so a block boundary can never land mid-surrogate: every boundary this
// module produces is a line boundary or a delimiter character.

/** Column alignment declared by a GFM delimiter row (`:--`, `:-:`, `--:`). */
export type ColumnAlignment = "left" | "center" | "right" | null;

/**
 * One table cell, addressed by the span it occupies in the source document.
 *
 * `text` is the cell's RAW markdown, `source.slice(start, end)` byte for byte —
 * never unescaped, never re-rendered. A real row reads
 * `| [PAR-9 – Rent roll import errors](https://linear.app/…) | Cool |`, and the
 * link survives only because nothing between the source and this string
 * interprets it. An escaped pipe (`a \| b`) likewise stays escaped: it is part of
 * the cell, not a delimiter (see `escapeTableCellText` for writing one back).
 *
 * The span EXCLUDES the padding spaces around the cell, so splicing a new value
 * leaves `| ` and ` |` untouched. An all-whitespace cell gets an empty span
 * anchored one character into its padding, which keeps `|  |` splicing to
 * `| x |` rather than `|x  |`.
 */
export interface TableCell {
  readonly text: string;
  readonly start: number;
  readonly end: number;
}

interface BlockSpan {
  /** Offset of the block's first character. */
  readonly start: number;
  /**
   * Offset one past the block's last character, INCLUDING its trailing line
   * terminator and the blank-line run that separates it from the next block.
   * Blocks are therefore contiguous and cover the whole document — there are no
   * filler blocks between them, and no byte belongs to two blocks.
   *
   * This is deliberately NOT the editable span: replacing `[start, end)` of a
   * block would swallow the blank line under it and weld two blocks together.
   * Editing goes through `textStart`/`textEnd` — every kind but `table`, whose
   * editable spans are its cells.
   */
  readonly end: number;
}

/**
 * The whitespace a replacement has to carry so the spliced line is STILL a
 * heading holding exactly that text. Each side is `""` or a single space.
 *
 * A heading with no text has a zero-width text span, and the byte on each side
 * of it is a `#`: `######` puts the span hard against the opening run, `# ###`
 * puts it hard against the closing one. Written naively, `Summary` welds to the
 * marker — `######Summary` is a PARAGRAPH, and `# Summary###` is a heading that
 * reads `Summary###`. Either way the edit changed something the user did not
 * name, which is the one thing this whole module exists to prevent.
 *
 * A table cell solves the same problem by anchoring its empty span one
 * character INTO the padding (`|  |` splices to `| x |`). A text-less heading
 * has no padding to anchor in, so the separator has to be written with the
 * text. `blockEdit` writes it; a caller assembling a `DocumentEdit` by hand
 * must too.
 */
export interface HeadingSeparators {
  /** Goes before the replacement when the `#` run abuts the text span. */
  readonly before: string;
  /** Goes after the replacement when a closing `#` run abuts the text span. */
  readonly after: string;
}

/** An ATX heading (`## Title`). Setext headings route to `raw` — see `RawBlockReason`. */
export interface HeadingBlock extends BlockSpan {
  readonly kind: "heading";
  readonly level: 1 | 2 | 3 | 4 | 5 | 6;
  /** Start of the editable text: after the `#` run and its following space. */
  readonly textStart: number;
  /** End of the editable text: before any trailing whitespace or closing `#` run. */
  readonly textEnd: number;
  /** `source.slice(textStart, textEnd)` — raw markdown, inline formatting preserved. */
  readonly text: string;
  /**
   * What a non-empty replacement must be wrapped in to keep this a heading.
   * Both sides are `""` for every heading that already carries text — only a
   * text-less one can have the marker sitting against its span.
   */
  readonly separators: HeadingSeparators;
}

/** A run of plain text lines. Its `text` may span several lines and hold inline markdown. */
export interface ParagraphBlock extends BlockSpan {
  readonly kind: "paragraph";
  /** Start of the editable text: the first character of the first line. */
  readonly textStart: number;
  /** End of the editable text: the last line's final character, before its terminator. */
  readonly textEnd: number;
  /** `source.slice(textStart, textEnd)` — raw markdown, inline formatting preserved. */
  readonly text: string;
}

/**
 * A GFM pipe table: a header row, a delimiter row, and zero or more body rows.
 *
 * Rows are stored exactly as written — a RAGGED row (fewer or more cells than the
 * header) keeps its own length rather than being padded or truncated, because
 * padding would invent cells that have no span to splice into.
 */
export interface TableBlock extends BlockSpan {
  readonly kind: "table";
  /** Convenience view of `headerCells`: `headers[i] === headerCells[i].text`. */
  readonly headers: readonly string[];
  /** The header cells, with the spans `headers` alone cannot carry. */
  readonly headerCells: readonly TableCell[];
  readonly rows: readonly (readonly TableCell[])[];
  /** One entry per header cell, from the delimiter row's `:` markers. */
  readonly alignments: readonly ColumnAlignment[];
}

/**
 * Why the scanner declined to model a span, for diagnostics and renderer hints.
 *
 * There is deliberately no `front-matter`. The renderer these blocks sit under
 * loads `gfm` and `codeMeta` and nothing else (`streamdown`'s
 * `defaultRemarkPlugins`, spread in `messages/MarkdownText.tsx`), so a leading
 * `---` is a thematic break to the user's eye and modelling it as front matter
 * would put an editing affordance where the render disagrees — and, when the
 * closing fence was never found, swallow the whole document into one raw block.
 */
export type RawBlockReason =
  | "blank"
  | "fenced-code"
  | "indented-code"
  | "html"
  | "blockquote"
  | "list"
  | "thematic-break"
  | "setext-heading";

/**
 * The catch-all: a span the scanner declines to MODEL, kept verbatim.
 *
 * Declining to model it is not the same as declining to edit it. A `raw` block
 * owns one contiguous text span like a paragraph does, so it is edited the same
 * way — in place, at its own position, by replacing exactly
 * `[textStart, textEnd)`. What it does not get is STRUCTURE: a list has no
 * per-item control, and a table drawn inside a code fence has no cells, because
 * nothing here claims to understand either.
 *
 * That is the safety valve, not a failure: an unmodelled construct is preserved
 * byte-identical instead of being mangled by a model that half-understands it.
 */
export interface RawBlock extends BlockSpan {
  readonly kind: "raw";
  readonly reason: RawBlockReason;
  /** Start of the editable text: the first character of the block's first line. */
  readonly textStart: number;
  /**
   * End of the editable text: the last CONTENT line's final character, before
   * its terminator. A `blank` run has no content line, so its span is empty.
   *
   * The gap between this and `end` is the blank-line run the block absorbed,
   * and keeping it OUT of the editable span is what stops an edited list being
   * welded to the paragraph beneath it. Blank lines that sit INSIDE the
   * construct — inside a fence, between two items of a loose list — are content
   * and stay in the span.
   */
  readonly textEnd: number;
  /** `source.slice(textStart, textEnd)` — the span verbatim, markdown and all. */
  readonly text: string;
}

/**
 * The blocks that carry a single editable text span (`blockEdit` takes these).
 *
 * A `table` is the one kind that does not: its editable spans are its cells,
 * one per `TableCell`, and there is no whole-table span to replace.
 */
export type EditableBlock = HeadingBlock | ParagraphBlock | RawBlock;

/** One block of a markdown document. */
export type DocumentBlock =
  | HeadingBlock
  | ParagraphBlock
  | TableBlock
  | RawBlock;

/** `DocumentBlock["kind"]`, named for callers that switch on it. */
export type DocumentBlockKind = DocumentBlock["kind"];

/**
 * One span replacement against the ORIGINAL source.
 *
 * Every offset in a batch is an offset into the same unedited string, which is
 * what lets `applyEdits` take an unordered batch: it never re-indexes, it walks
 * the original once. Build these with `cellEdit` / `headerCellEdit` / `blockEdit`
 * rather than by hand.
 */
export interface DocumentEdit {
  readonly start: number;
  readonly end: number;
  /** Written VERBATIM. Nothing here escapes, trims, or reformats it. */
  readonly text: string;
}
