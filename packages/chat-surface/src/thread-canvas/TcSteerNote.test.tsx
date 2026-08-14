import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { ReactElement } from "react";

import { TcSteerNote } from "./TcSteerNote";

const here =
  typeof import.meta.dirname === "string"
    ? import.meta.dirname
    : dirname(fileURLToPath(import.meta.url));

let sheet: HTMLStyleElement | null = null;

/**
 * Mount the real `review-surfaces.css` before rendering.
 *
 * jsdom performs no layout, so a green DOM assertion says nothing about whether
 * the row is legible — the same trap that once clipped a disclosure to 6% of its
 * ink under a fully green suite. What CAN be asserted honestly is the declared
 * CONTRACT: which item yields, which does not, and what register the text is in.
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

describe("TcSteerNote", () => {
  it("prints the server's sentence and the user's words beneath it", () => {
    render(
      <TcSteerNote
        label="You steered this run."
        text="Use the staging table, not production."
      />,
    );
    expect(screen.getByTestId("tc-steer-label").textContent).toBe(
      "You steered this run.",
    );
    expect(screen.getByTestId("tc-steer-text").textContent).toBe(
      "Use the staging table, not production.",
    );
  });

  it("announces the whole aside, not a bare rule", () => {
    render(<TcSteerNote label="You steered this run." text="Stop and wait." />);
    // The label is announced first, so a reader who cannot see the rule still
    // learns this is an interjection before they hear the quotation.
    expect(
      screen.getByRole("group", { name: "You steered this run." }),
    ).toBeTruthy();
  });

  it("is a note, not a card: nothing on it can be pressed or opened", () => {
    withRealCss(
      <TcSteerNote label="You steered this run." text="Stop and wait." />,
    );
    const root = screen.getByTestId("tc-steer");
    // Every framed object in this transcript is something the reader can act on.
    // A steer has already been sent and accepted — there is nothing to decide.
    expect(root.querySelectorAll("button")).toHaveLength(0);
    expect(root.querySelectorAll("a")).toHaveLength(0);
    expect(globalThis.getComputedStyle(root).display).toBe("flex");
  });

  it("draws its rule on the LEADING edge, and gives it no share of the leftover", () => {
    withRealCss(
      <TcSteerNote label="You steered this run." text="Stop and wait." />,
    );
    const rules = document.querySelectorAll(".tc-steer__rule");
    // ONE rule, not the compaction divider's two: a centred pair would claim
    // this is a property of the transcript, and it is not — it came from one
    // side of the conversation.
    expect(rules).toHaveLength(1);
    const style = globalThis.getComputedStyle(rules[0]);
    // The anchor of the row, not a spacer: `flex: none` is what stops it
    // absorbing the width the way the compaction rules deliberately do.
    expect(style.flexGrow).toBe("0");
    expect(style.flexShrink).toBe("0");
    // A hairline that actually paints. Width is the half of "is it visible"
    // jsdom can answer honestly.
    expect(style.width).toBe("2px");
    expect(style.alignSelf).toBe("stretch");
  });

  it("wraps the user's words and never ellipsises them, however long", () => {
    withRealCss(
      <TcSteerNote
        label="You steered this run."
        text={`Use ${"x".repeat(400)} instead`}
      />,
    );
    const style = globalThis.getComputedStyle(
      screen.getByTestId("tc-steer-text"),
    );
    // Clipping the words someone sent into their own run, in the one place the
    // record of having sent them exists, would defeat the row. The server
    // already bounds a steer at 4000 characters before it is ever accepted, so
    // there is no unbounded string to defend against here.
    expect(style.overflowWrap).toBe("anywhere");
    expect(style.textOverflow).not.toBe("ellipsis");
    expect(style.whiteSpace).toBe("pre-wrap");
  });

  it("lets the body take the leftover, with a floor of zero so a long token wraps", () => {
    withRealCss(
      <TcSteerNote label="You steered this run." text="Stop and wait." />,
    );
    const style = globalThis.getComputedStyle(
      document.querySelector(".tc-steer__body") as Element,
    );
    expect(style.flexGrow).toBe("1");
    // Without this a flex child's intrinsic minimum is its longest word, so an
    // unbroken token pushes the row wider than the chat column instead of
    // wrapping inside it.
    expect(style.minWidth).toBe("0px");
  });

  it("keeps the label quieter than the words it frames", () => {
    withRealCss(
      <TcSteerNote label="You steered this run." text="Stop and wait." />,
    );
    const label = globalThis.getComputedStyle(
      screen.getByTestId("tc-steer-label"),
    );
    const text = globalThis.getComputedStyle(
      screen.getByTestId("tc-steer-text"),
    );
    // The framing sentence is not the content. Same register the compaction
    // label sits in; the quotation is a step up from it.
    expect(label.color).not.toBe(text.color);
    expect(label.fontSize).not.toBe(text.fontSize);
  });
});
