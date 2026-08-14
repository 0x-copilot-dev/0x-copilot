import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { ReactElement } from "react";

import { TcCompactionDivider } from "./TcCompactionDivider";

const here =
  typeof import.meta.dirname === "string"
    ? import.meta.dirname
    : dirname(fileURLToPath(import.meta.url));

let sheet: HTMLStyleElement | null = null;

/**
 * Mount the real `review-surfaces.css` before rendering.
 *
 * jsdom performs no layout, so a green DOM assertion says nothing about whether
 * the divider is on screen — the same trap that once clipped a disclosure to 6%
 * of its ink under a fully green suite. What CAN be asserted honestly is the
 * declared CONTRACT: which item yields, which does not, and what register the
 * text is in. That is what decides the outcome once a browser lays it out.
 */
function withRealCss(node: ReactElement): void {
  sheet = document.createElement("style");
  sheet.textContent = readFileSync(
    resolve(here, "review-surfaces.css"),
    "utf-8",
  );
  document.head.appendChild(sheet);
  render(node);
}

afterEach(() => {
  sheet?.remove();
  sheet = null;
});

describe("TcCompactionDivider", () => {
  it("prints the server's sentence and the two counts beside it", () => {
    render(
      <TcCompactionDivider
        label="Compacted 8.6k tokens of read_file output"
        beforeTokens={8900}
        afterTokens={300}
      />,
    );
    expect(screen.getByTestId("tc-compaction-label").textContent).toBe(
      "Compacted 8.6k tokens of read_file output",
    );
    expect(screen.getByTestId("tc-compaction-counts").textContent).toBe(
      "8.9k → 300",
    );
  });

  it("rounds the counts the way the server's title does, so the row agrees with itself", () => {
    render(
      <TcCompactionDivider
        label="Compacted 24k tokens of tool output"
        beforeTokens={24_400}
        afterTokens={1000}
      />,
    );
    // `Messages.Event._compact_token_count`: <1k exact, <10k one decimal with a
    // trailing `.0` folded away, else rounded thousands.
    expect(screen.getByTestId("tc-compaction-counts").textContent).toBe(
      "24k → 1k",
    );
  });

  it("drops the counts entirely when the wire carried only one end", () => {
    render(
      <TcCompactionDivider
        label="Compacted 900 tokens of tool output"
        beforeTokens={1200}
      />,
    );
    // A one-sided arrow is a measurement nobody made.
    expect(screen.queryByTestId("tc-compaction-counts")).toBeNull();
  });

  it("announces the whole sentence, not a bare separator", () => {
    render(
      <TcCompactionDivider label="Compacted 8.6k tokens of tool output" />,
    );
    // `role="separator"` would carry a name most AT never reads out. The
    // sentence IS the content here, so the boundary is a labelled group.
    expect(
      screen.getByRole("group", {
        name: "Compacted 8.6k tokens of tool output",
      }),
    ).toBeTruthy();
  });

  it("is a boundary, not a card: nothing on it can be pressed or opened", () => {
    withRealCss(
      <TcCompactionDivider
        label="Compacted 8.6k tokens of read_file output"
        beforeTokens={8900}
        afterTokens={300}
      />,
    );
    const root = screen.getByTestId("tc-compaction");
    // Every other object in this transcript is framed because it can be acted
    // on. This one is a statement about the transcript, so it grows no control
    // and no disclosure — that is the whole distinction being drawn.
    expect(root.querySelectorAll("button")).toHaveLength(0);
    expect(root.querySelectorAll("a")).toHaveLength(0);
    expect(root.tagName).toBe("DIV");
    expect(globalThis.getComputedStyle(root).display).toBe("flex");
  });

  it("wraps the sentence rather than ellipsising it, whatever the tool is called", () => {
    withRealCss(
      <TcCompactionDivider
        label={`Compacted 8.6k tokens of ${"x".repeat(140)} output`}
      />,
    );
    const style = globalThis.getComputedStyle(
      screen.getByTestId("tc-compaction-label"),
    );
    // The label is the ONLY content on this row. If it were the item that gave
    // way, a narrow column would draw a rule with nothing legible in it — a
    // boundary the reader cannot account for, which is worse than no boundary.
    expect(style.overflowWrap).toBe("anywhere");
    expect(style.whiteSpace).not.toBe("nowrap");
    expect(style.textAlign).toBe("center");
  });

  it("makes the RULES the items that yield, and gives them equal basis", () => {
    withRealCss(<TcCompactionDivider label="Compacted 8.6k tokens" />);
    const rules = document.querySelectorAll(".tc-compaction__rule");
    expect(rules).toHaveLength(2);
    for (const rule of rules) {
      const style = globalThis.getComputedStyle(rule);
      // `flex: 1` ⇒ basis 0 ⇒ the two sides split the leftover evenly, so the
      // sentence sits centred however long it is.
      expect(style.flexGrow).toBe("1");
      expect(style.flexBasis).toBe("0%");
      expect(style.minWidth).toBe("0px");
      // A hairline that actually paints. Height is the half of "is it visible"
      // jsdom can answer honestly.
      expect(style.height).toBe("1px");
    }
  });

  it("never lets the counts shrink — a half-printed number is a wrong number", () => {
    withRealCss(
      <TcCompactionDivider
        label="Compacted 8.6k tokens of read_file output"
        beforeTokens={8900}
        afterTokens={300}
      />,
    );
    const style = globalThis.getComputedStyle(
      screen.getByTestId("tc-compaction-counts"),
    );
    expect(style.flexShrink).toBe("0");
    expect(style.whiteSpace).toBe("nowrap");
  });
});
