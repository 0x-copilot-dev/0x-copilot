import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import type { Slide } from "./SlideRenderer";
import { SlideDiff, slideAdapter, type SlideDiffPayload } from "./SlideDiff";

const BEFORE: Slide = {
  slideId: "slide-1-before",
  deckId: "deck-acme-q4",
  slideNumber: 3,
  title: "Q4 Revenue",
  bullets: [{ text: "ARR up 18% YoY" }, { text: "Net retention 110%" }],
  thumbnailUrl: "https://example.invalid/before.png",
};

const AFTER: Slide = {
  slideId: "slide-1-after",
  deckId: "deck-acme-q4",
  slideNumber: 3,
  title: "Q4 Revenue Bridge",
  bullets: [
    { text: "ARR up 22% YoY" },
    { text: "Net retention 118%" },
    { text: "Pipeline coverage 3.4x" },
  ],
  thumbnailUrl: "https://example.invalid/after.png",
};

const DIFF_WITH_SUMMARY: SlideDiffPayload = {
  diffId: "diff-slide-1",
  before: BEFORE,
  after: AFTER,
  summary: "Update slide 3 with refreshed Q4 numbers",
  provenance: "DRAFTED FROM Q4 FINANCE EXPORT",
};

const DIFF_NO_SUMMARY: SlideDiffPayload = {
  diffId: "diff-slide-2",
  before: BEFORE,
  after: AFTER,
};

describe("SlideDiff", () => {
  it("renders both BEFORE and AFTER regions", () => {
    render(<SlideDiff diff={DIFF_WITH_SUMMARY} />);
    expect(screen.getByTestId("slide-diff-before")).toBeInTheDocument();
    expect(screen.getByTestId("slide-diff-after")).toBeInTheDocument();
  });

  it("labels each region", () => {
    render(<SlideDiff diff={DIFF_WITH_SUMMARY} />);
    expect(screen.getByTestId("slide-diff-before-label")).toHaveTextContent(
      "Before",
    );
    expect(screen.getByTestId("slide-diff-after-label")).toHaveTextContent(
      "After",
    );
  });

  it("dims the BEFORE region (opacity 0.6) and keeps AFTER at full opacity", () => {
    render(<SlideDiff diff={DIFF_WITH_SUMMARY} />);
    const before = screen.getByTestId("slide-diff-before");
    const after = screen.getByTestId("slide-diff-after");
    expect(before.style.opacity).toBe("0.6");
    expect(after.style.opacity).toBe("1");
  });

  it("exposes the diff id via data-diff-id", () => {
    render(<SlideDiff diff={DIFF_WITH_SUMMARY} />);
    expect(screen.getByTestId("slide-diff")).toHaveAttribute(
      "data-diff-id",
      DIFF_WITH_SUMMARY.diffId,
    );
  });

  it("renders the summary annotation via TcInlineDiff when supplied", () => {
    render(<SlideDiff diff={DIFF_WITH_SUMMARY} />);
    expect(screen.getByTestId("slide-diff-annotation")).toBeInTheDocument();
    expect(screen.getByTestId("tc-inline-diff-pill")).toBeInTheDocument();
    expect(
      screen.getByText(DIFF_WITH_SUMMARY.summary as string),
    ).toBeInTheDocument();
  });

  it("omits the annotation when summary is absent", () => {
    render(<SlideDiff diff={DIFF_NO_SUMMARY} />);
    expect(
      screen.queryByTestId("slide-diff-annotation"),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("tc-inline-diff-pill")).not.toBeInTheDocument();
  });

  it("does not render any Approve/Reject buttons (host owns those — D28)", () => {
    render(<SlideDiff diff={DIFF_WITH_SUMMARY} />);
    expect(
      screen.queryByTestId("tc-inline-diff-approve"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("tc-inline-diff-reject"),
    ).not.toBeInTheDocument();
  });
});

describe("slideAdapter renderDiff", () => {
  it("returns an element that renders the diff regions", () => {
    const element = slideAdapter.renderDiff(DIFF_WITH_SUMMARY);
    render(element);
    expect(screen.getByTestId("slide-diff")).toBeInTheDocument();
    expect(screen.getByTestId("slide-diff-before")).toBeInTheDocument();
    expect(screen.getByTestId("slide-diff-after")).toBeInTheDocument();
  });
});
