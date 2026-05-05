// PR 3.5 / G8 — streaming-cursor regression contract.
//
// PR 2.3 §3.7 documented this as a "sanity check that the cursor still
// appears on the last paragraph after CSS change". The actual cursor is
// a Streamdown internal — the user-visible affordance we own is the
// `assistant-markdown--streaming` class. Two contracts:
//
//   1. The class is present iff the part is streaming. CSS keys off it
//      to disable the animation under `prefers-reduced-motion`.
//   2. Switching to `running` then `complete` toggles the class back —
//      i.e. the class doesn't stick after the run finishes.

import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";

// Stub Streamdown to a plain wrapper so the test isolates *our* class
// management, not the library's internal animation. We assert the
// className we pass to Streamdown — that's the contract our CSS rules
// (`@media (prefers-reduced-motion: reduce) { .assistant-markdown--streaming }`)
// hang off.
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
}));

vi.mock("./MarkdownLink", () => ({ MarkdownLink: () => null }));

import { MarkdownText } from "./MarkdownText";

const RUNNING = { type: "running" } as never;
const COMPLETE = { type: "complete" } as never;

describe("streaming cursor (PR 3.5 / G8)", () => {
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
