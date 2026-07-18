// Streaming-cursor wiring contract (FR-1.1). The user-visible affordance
// we own is the `assistant-markdown--streaming` class; Streamdown's actual
// cursor is a library internal. Two contracts:
//
//   1. The class is present iff the part is streaming. CSS keys off it to
//      disable the animation under `prefers-reduced-motion`.
//   2. Switching from `running` to `complete` toggles the class back — it
//      doesn't stick after the run finishes.
//
// The contract itself lives in `streamingCursorProps()` (single source of
// truth across any future streaming-text surface); MarkdownText spreads it
// onto Streamdown. This is the integration check that MarkdownText is still
// wired to that contract. Streamdown is stubbed to a plain wrapper so the
// test isolates *our* class management, not the library's animation.

import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("streamdown", () => ({
  Streamdown: ({
    className,
    children,
  }: {
    className?: string;
    children?: unknown;
    [key: string]: unknown;
  }): ReactElement => (
    <div data-testid="streamdown" className={className}>
      {String(children ?? "")}
    </div>
  ),
  // MarkdownText re-spreads Streamdown's default remark plugins ahead of the
  // citation plugin (so GFM survives); the stub just needs the export present.
  defaultRemarkPlugins: {},
}));

import { MarkdownText } from "./MarkdownText";

const RUNNING = { type: "running" } as never;
const COMPLETE = { type: "complete" } as never;

describe("streaming cursor", () => {
  it("sets `assistant-markdown--streaming` while the part is running", () => {
    const { getByTestId } = render(
      <MarkdownText
        type="text"
        text="Per the [c1] positioning…"
        status={RUNNING}
      />,
    );
    const node = getByTestId("streamdown");
    expect(node.className).toContain("assistant-markdown");
    expect(node.className).toContain("assistant-markdown--streaming");
  });

  it("drops the streaming class on completion", () => {
    const { getByTestId } = render(
      <MarkdownText type="text" text="Done." status={COMPLETE} />,
    );
    const node = getByTestId("streamdown");
    expect(node.className).toContain("assistant-markdown");
    expect(node.className).not.toContain("assistant-markdown--streaming");
  });
});
