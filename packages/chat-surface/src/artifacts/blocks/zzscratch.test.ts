import { describe, it } from "vitest";
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import { toHast } from "mdast-util-to-hast";

import { MARKDOWN_CORPUS } from "./corpus";

const processor = unified().use(remarkParse).use(remarkGfm);

function cellsPerRenderedRow(source: string): unknown {
  const hast = toHast(processor.parse(source)) as {
    children: { type: string; tagName?: string; children?: unknown[] }[];
  };
  const out: unknown[] = [];
  const walk = (node: { tagName?: string; children?: unknown[] }): void => {
    if (node.tagName === "tr") {
      out.push(
        (node.children ?? [])
          .filter(
            (c) =>
              (c as { tagName?: string }).tagName === "td" ||
              (c as { tagName?: string }).tagName === "th",
          )
          .map((c) => JSON.stringify((c as { children?: unknown[] }).children)),
      );
    }
    for (const child of node.children ?? [])
      walk(child as { tagName?: string; children?: unknown[] });
  };
  walk(hast as unknown as { children?: unknown[] });
  return out;
}

describe("oracle html", () => {
  it("ragged excess cells in the render", () => {
    // eslint-disable-next-line no-console
    console.log(
      "\nRAGGED rendered rows:\n" +
        JSON.stringify(
          cellsPerRenderedRow(MARKDOWN_CORPUS.RAGGED_TABLE),
          null,
          1,
        ),
    );
    // eslint-disable-next-line no-console
    console.log(
      "\nTWO-COL, 4-cell row rendered:\n" +
        JSON.stringify(
          cellsPerRenderedRow("| A | B |\n| --- | --- |\n| 1 | 2 | 3 | 4 |\n"),
          null,
          1,
        ),
    );
  });
});
