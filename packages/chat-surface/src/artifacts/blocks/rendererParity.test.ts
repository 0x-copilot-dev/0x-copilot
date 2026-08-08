// The differential suite: `parseBlocks` against the renderer it sits under.
//
// `parseBlocks.test.ts` pins classification against hand-written expectations,
// which is exactly the test a permissive scanner passes — the expectation was
// written by reading the same code. This file asserts against remark-gfm
// itself, the parser Streamdown drives in `messages/MarkdownText.tsx`, so a rule
// the scanner made up cannot be confirmed by the test that asserts it.
//
// It found the case it was written for, and five more. In order of discovery:
//
//   1. `a | b` over `- | ---` was a TABLE to the scanner and a paragraph plus a
//      list to remark — `-` is both a delimiter character and a bullet, and the
//      bullet wins — so the editor mounted a grid over what the user was reading
//      as prose. This is the reported defect;
//   2. HTML block type 7 (`<br/>`) cannot interrupt a paragraph, and treating it
//      as a block start ran the raw run to the next blank line, taking the
//      heading written under it with it. `<span>x</span>` is not even type 7,
//      and a delimiter row under it really does make a table;
//   3. a table that cut a paragraph short was re-dispatched on its own header
//      line and came back a list;
//   4. lazy continuation needs an OPEN paragraph — `> # h`, an empty `-`, and a
//      heading inside an item all close one, and consuming the next line anyway
//      buried whatever followed;
//   5. an item that could not have interrupted a paragraph still ends a QUOTE,
//      the one place a container is stricter than a paragraph;
//   6. an HTML block inside an item runs to the next blank line and swallows the
//      nested bullets under it, so the delimiter row below starts a table
//      OUTSIDE the list rather than continuing it.
//
// None of the six is visible in `MARKDOWN_CORPUS`. Every one came out of the
// generated near-miss documents, which is the argument for keeping them.
//
// ## What is asserted, and why it is not node-for-node equality
//
// A `raw` block is a deliberate catch-all that may cover SEVERAL of remark's
// nodes — `blockModel.ts` says so, and `TABLE_SWALLOWED_BY_LIST` depends on it.
// So the contract is about the blocks the scanner claims to MODEL:
//
//   1. every `heading` / `paragraph` / `table` block starts exactly where a
//      remark node of the same type starts, and covers no second node;
//   2. no remark `table` starts inside a block that is not one — the scanner may
//      not MISS a grid the user can see;
//   3. `parseBlocks(s).map(slice).join("") === s` still holds, on every document
//      this file generates.
//
// Rule 3 is repeated from `roundTrip.test.ts` on purpose: these documents are
// the adversarial ones, and coverage is the property every other one rests on.

import { describe, expect, it } from "vitest";
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import { toHast } from "mdast-util-to-hast";

import {
  MARKDOWN_CORPUS,
  generateDocument,
  generateNearMissDocument,
  mulberry32,
  nearMissPairs,
} from "./corpus";
import { parseBlocks } from "./parseBlocks";
import type { DocumentBlock } from "./blockModel";

const processor = unified().use(remarkParse).use(remarkGfm);

interface RenderedNode {
  readonly type: string;
  readonly start: number;
  readonly end: number;
}

/** The oracle: what remark-gfm makes of the document, as top-level spans. */
function render(source: string): RenderedNode[] {
  const root = processor.parse(source) as unknown as {
    children: {
      type: string;
      position?: { start: { offset: number }; end: { offset: number } };
    }[];
  };
  return root.children.map((child) => ({
    type: child.type,
    start: child.position?.start.offset ?? -1,
    end: child.position?.end.offset ?? -1,
  }));
}

function label(block: DocumentBlock): string {
  return block.kind === "raw" ? `raw:${block.reason}` : block.kind;
}

/** How many cells each row carries once remark-gfm has RENDERED the table. */
function renderedRowArity(source: string): number[] {
  const rows: number[] = [];
  const walk = (node: { tagName?: string; children?: unknown[] }): void => {
    if (node.tagName === "tr") {
      rows.push(
        (node.children ?? []).filter((child) =>
          ["td", "th"].includes((child as { tagName?: string }).tagName ?? ""),
        ).length,
      );
    }
    for (const child of node.children ?? []) {
      walk(child as { tagName?: string; children?: unknown[] });
    }
  };
  walk(toHast(processor.parse(source)) as unknown as { children?: unknown[] });
  return rows;
}

/**
 * Every way this document's blocks disagree with the render, as one line each.
 *
 * Empty is the passing answer. The strings are the assertion's failure message,
 * so they name the block, what remark put there, and nothing else.
 */
function disagreements(source: string): string[] {
  const nodes = render(source);
  const found: string[] = [];

  for (const block of parseBlocks(source)) {
    const covered = nodes.filter(
      (node) => node.start >= block.start && node.start < block.end,
    );

    if (block.kind === "raw") {
      // Rule 2. A `raw` block may group several nodes, but a table inside one is
      // a grid the user sees and the editor does not.
      const table = covered.find((node) => node.type === "table");
      if (table !== undefined) {
        found.push(
          `${label(block)} at ${block.start} hides a table at ${table.start}`,
        );
      }
      continue;
    }

    // Rule 1, for the three kinds the scanner claims to model.
    if (covered.length === 0) {
      found.push(`${block.kind} at ${block.start} renders as nothing`);
      continue;
    }
    if (covered[0].type !== block.kind) {
      found.push(
        `${block.kind} at ${block.start} renders as ${covered.map((node) => node.type).join(" + ")}`,
      );
      continue;
    }
    if (covered.length > 1) {
      found.push(
        `${block.kind} at ${block.start} covers ${covered.map((node) => node.type).join(" + ")}`,
      );
    }
  }

  return found;
}

/**
 * The two classes this suite has measured and deliberately does NOT fix, each
 * with the reason. Documents matching one are excluded from the strict sweep and
 * pinned individually under "known divergences" below, so the sweep stays at
 * zero tolerance for anything new.
 */
const KNOWN_DIVERGENCE_MATCHERS: readonly ((lines: string[]) => boolean)[] = [
  // A link reference definition renders as NOTHING and the scanner reads it as
  // prose. Modelling it means implementing CommonMark's label / destination /
  // title grammar, and a partial implementation trades this divergence for a
  // worse one — a line the scanner calls a definition and remark calls a
  // paragraph, which is the direction that loses a user's text.
  (lines) => lines.some((line) => /^ {0,3}\[[^\]]+\]:/.test(line)),
  // remark reads an item that cannot interrupt a paragraph — an empty one, or an
  // ordered one numbered anything but 1 — as PROSE when it follows an indented
  // code block, and as a list everywhere else. Only INDENTED code does it: a
  // fence, a heading, a rule and an HTML block all leave it a list. It is a
  // quirk of the renderer's flow ordering with no rule behind it worth encoding,
  // and both readings put one editable text span over the same bytes.
  (lines) =>
    lines.some((line, index) => {
      if (!/^ {0,3}(?:[-+*][ \t]*$|\d{1,9}[.)](?:[ \t]*$|[ \t]+\S))/.test(line))
        return false;
      if (/^ {0,3}1[.)][ \t]+\S/.test(line)) return false;
      // The blank line between them does not clear it.
      let above = index - 1;
      while (above >= 0 && lines[above].trim().length === 0) above -= 1;
      return above >= 0 && /^(?: {4}|\t)/.test(lines[above]);
    }),
];

function isKnownDivergence(source: string): boolean {
  const lines = source.split(/\r\n|\n|\r/);
  return KNOWN_DIVERGENCE_MATCHERS.some((matches) => matches(lines));
}

function assertAgrees(source: string, where: string): void {
  if (isKnownDivergence(source)) return;
  const found = disagreements(source);
  if (found.length > 0) {
    throw new Error(
      `${where} disagrees with remark-gfm:\n${JSON.stringify(source)}\n  ${found.join("\n  ")}`,
    );
  }
}

/** Rule 3, asserted here too because these documents are the adversarial ones. */
function assertCovers(source: string, where: string): void {
  const rejoined = parseBlocks(source)
    .map((block) => source.slice(block.start, block.end))
    .join("");
  if (rejoined !== source) {
    throw new Error(
      `${where} lost bytes: ${JSON.stringify(source)} != ${JSON.stringify(rejoined)}`,
    );
  }
}

describe("parseBlocks agrees with remark-gfm", () => {
  for (const [name, source] of Object.entries(MARKDOWN_CORPUS)) {
    it(`on ${name}`, () => {
      assertAgrees(source, name);
      assertCovers(source, name);
    });
  }

  it("on the document from the bug report, node for node", () => {
    // The one case worth spelling out rather than asserting generically: the
    // heading, the table and the prose the whole module exists for.
    expect(
      render(MARKDOWN_CORPUS.BUG_DOCUMENT).map((node) => node.type),
    ).toEqual(["heading", "table", "paragraph"]);
    expect(parseBlocks(MARKDOWN_CORPUS.BUG_DOCUMENT).map(label)).toEqual([
      "heading",
      "table",
      "paragraph",
    ]);
  });

  it("on every pair of near-miss lines", () => {
    let checked = 0;
    for (const source of nearMissPairs()) {
      assertAgrees(source, `pair ${JSON.stringify(source)}`);
      assertCovers(source, `pair ${JSON.stringify(source)}`);
      checked += 1;
    }
    // The alphabet is exhaustive over itself; if it shrinks, this notices.
    expect(checked).toBeGreaterThan(2500);
  });

  it("on 8000 generated near-miss documents", () => {
    // Three to ten lines of pure near-miss alphabet. The classes this suite
    // closed showed up at three lines and at six; a longer document is where the
    // container state (an open HTML block, a nested item's content column) has
    // room to be wrong.
    const random = mulberry32(0xbee5);
    for (let iteration = 0; iteration < 8000; iteration += 1) {
      const lineCount = 3 + Math.floor(random() * 8);
      const source = generateNearMissDocument(random, lineCount);
      assertAgrees(source, `near-miss iteration ${iteration}`);
      assertCovers(source, `near-miss iteration ${iteration}`);
    }
  });

  it("on 500 generated whole-construct documents", () => {
    const random = mulberry32(0x5eed);
    for (let iteration = 0; iteration < 500; iteration += 1) {
      const source = generateDocument(random);
      assertAgrees(source, `construct iteration ${iteration}`);
      assertCovers(source, `construct iteration ${iteration}`);
    }
  });
});

describe("the divergence classes this suite was written to close", () => {
  it("does not read a bullet under prose as a table", () => {
    // The reported defect. `-` is a legal delimiter cell AND a bullet, and the
    // bullet wins in remark-gfm, so nothing here is a table.
    const source = "a | b\n- | ---\n";
    expect(render(source).map((node) => node.type)).toEqual([
      "paragraph",
      "list",
    ]);
    expect(parseBlocks(source).map(label)).toEqual(["paragraph", "raw:list"]);
  });

  it("does not let an inline tag swallow the block written under it", () => {
    // `<br/>` is HTML block type 7, which cannot interrupt a paragraph. Treating
    // it as a block start ran the raw run to the next blank line and took the
    // heading with it.
    const source = "a\n<br/>\n# h\n";
    expect(render(source).map((node) => node.type)).toEqual([
      "paragraph",
      "heading",
    ]);
    expect(parseBlocks(source).map(label)).toEqual(["paragraph", "heading"]);
  });

  it("still lets a real HTML block interrupt a paragraph", () => {
    // The other half of the same rule: `<div>` is type 6 and does interrupt.
    const source = "a\n<div>\nx\n";
    expect(render(source).map((node) => node.type)).toEqual([
      "paragraph",
      "html",
    ]);
    expect(parseBlocks(source).map(label)).toEqual(["paragraph", "raw:html"]);
  });

  it("reads a table that cut a paragraph short as a table, not as a list", () => {
    // `2.` cannot start a list under an open paragraph, so it is still prose
    // when the delimiter row underneath turns it into a header row. Dispatching
    // on that line afresh read it as the list it looks like in isolation.
    const source = "a\n2. x | y\n--- | ---\n";
    expect(render(source).map((node) => node.type)).toEqual([
      "paragraph",
      "table",
    ]);
    expect(parseBlocks(source).map(label)).toEqual(["paragraph", "table"]);
  });

  it("gives an inline tag back to the flow when a delimiter row ends the paragraph", () => {
    // The exception to the rule above, also measured: an HTML block start wins
    // the line back once the paragraph has ended, so this is a paragraph and an
    // HTML block rather than a table headed by `<br/>`.
    const source = "a\n<br/>\n| - |\n";
    expect(render(source).map((node) => node.type)).toEqual([
      "paragraph",
      "html",
    ]);
    expect(parseBlocks(source).map(label)).toEqual(["paragraph", "raw:html"]);
  });

  it("does not continue an empty list item lazily", () => {
    // An empty item has no paragraph to continue, so the `a` below belongs to
    // the setext heading and not to the list. Consuming it lazily stranded the
    // `---` as a thematic break.
    const source = "-\na\n---\n";
    expect(render(source).map((node) => node.type)).toEqual([
      "list",
      "heading",
    ]);
    expect(parseBlocks(source).map(label)).toEqual([
      "raw:list",
      "raw:setext-heading",
    ]);
  });

  it("ends a quote at a list item that could not have interrupted a paragraph", () => {
    // The container/paragraph asymmetry. `a` / `2. x` is one paragraph, because
    // an ordered item numbered anything but 1 cannot interrupt one — but an
    // unmarked line only continues a QUOTE if it would be paragraph text on its
    // own, and this one opens a list. Carrying it into the quote buried the
    // heading under it inside a raw block.
    const source = "> q\n2. x\n   # a\n";
    expect(render(source).map((node) => node.type)).toEqual([
      "blockquote",
      "list",
    ]);
    expect(parseBlocks(source).map(label)).toEqual([
      "raw:blockquote",
      "raw:list",
    ]);
  });

  it("lets an HTML block inside an item swallow the bullet under it", () => {
    // `<br/>` opens an HTML block INSIDE the item, and that block runs to the
    // next blank line — so the indented `- x` is its content and not a nested
    // item, and the delimiter row two lines down starts a table OUTSIDE the
    // list. Reading that `- x` as a nested item made the item look like it had
    // reopened a paragraph, and the table vanished into the raw block.
    const source = "* x\n<br/>\n    - x\n-- | --\n--:|:--\n";
    expect(render(source).map((node) => node.type)).toEqual(["list", "table"]);
    expect(parseBlocks(source).map(label)).toEqual(["raw:list", "table"]);
  });

  it("keeps a line indented into the OUTER item inside the same list", () => {
    // `   # a` is too shallow for the nested `  - x` (content at column 4) and
    // deep enough for the outer `- | ---` (content at 2), so it is a heading
    // inside that outer item and the list runs on. Measuring ownership against
    // the innermost item alone cut the list short here.
    const source = "- | ---\n  - x\n   # a\n";
    expect(render(source).map((node) => node.type)).toEqual(["list"]);
    expect(parseBlocks(source).map(label)).toEqual(["raw:list"]);
  });
});

describe("what the block model deliberately reads differently", () => {
  it("keeps a ragged row's own cells where the render pads and truncates", () => {
    // Folded in from the scratch oracle this suite replaced. remark-gfm renders
    // every body row at the HEADER's arity — a short row gains an empty cell, an
    // over-long one loses its extra — while `TableBlock` keeps the row exactly as
    // written. That is not a bug to close: a padded cell has no span in the
    // source to splice into, and a truncated one is still text the user typed
    // and must survive a round trip. It is recorded here because it is a real
    // difference between the model and the render, in the CELL dimension rather
    // than the block one.
    const source = MARKDOWN_CORPUS.RAGGED_TABLE;
    const table = parseBlocks(source).find((block) => block.kind === "table");
    if (table?.kind !== "table") throw new Error("expected a table");
    expect(table.rows.map((row) => row.length)).toEqual([3, 2, 4]);
    expect(renderedRowArity(source)).toEqual([3, 3, 3, 3]);
    // Same in a two-column table: the extra cells are dropped from the render
    // and kept by the model.
    const wide = "| A | B |\n| --- | --- |\n| 1 | 2 | 3 | 4 |\n";
    expect(renderedRowArity(wide)).toEqual([2, 2]);
    const wideTable = parseBlocks(wide).find((block) => block.kind === "table");
    if (wideTable?.kind !== "table") throw new Error("expected a table");
    expect(wideTable.rows[0]).toHaveLength(4);
  });
});

describe("known divergences from remark-gfm, measured and left alone", () => {
  it("reads a link reference definition as prose", () => {
    // remark emits a `definition`, which renders as nothing at all. The scanner
    // calls it a paragraph and, worse, welds it to the prose underneath. Left as
    // is because closing it means implementing the definition grammar, and a
    // partial one mislabels real prose — see KNOWN_DIVERGENCE_MATCHERS.
    const source = "[a]: /b\nprose\n";
    expect(render(source).map((node) => node.type)).toEqual([
      "definition",
      "paragraph",
    ]);
    expect(parseBlocks(source).map(label)).toEqual(["paragraph"]);
    expect(disagreements(source)).not.toEqual([]);
  });

  it("reads an item that cannot interrupt, under indented code, as a list", () => {
    // remark says prose here and says list everywhere else, with no rule behind
    // the difference. Both readings put one editable text span over `-`.
    const source = "    code\n-\n";
    expect(render(source).map((node) => node.type)).toEqual([
      "code",
      "paragraph",
    ]);
    expect(parseBlocks(source).map(label)).toEqual([
      "raw:indented-code",
      "raw:list",
    ]);
  });

  it("keeps both classes inside the matchers that exclude them", () => {
    // The matchers are the reason the sweep above can stay at zero tolerance.
    // If one stops matching its own case, the sweep starts reporting it again —
    // which is the intended outcome, but this test says so out loud first.
    expect(isKnownDivergence("[a]: /b\nprose\n")).toBe(true);
    expect(isKnownDivergence("    code\n-\n")).toBe(true);
    expect(isKnownDivergence(MARKDOWN_CORPUS.BUG_DOCUMENT)).toBe(false);
    expect(isKnownDivergence("a | b\n- | ---\n")).toBe(false);
  });
});
