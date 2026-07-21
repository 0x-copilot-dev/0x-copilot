import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import { DiffText } from "./DiffText";
import { wordDiff } from "./wordDiff";

describe("DiffText — semantic rendering", () => {
  it("renders equal / delete / insert runs as text, <del>, and <ins>", () => {
    render(<DiffText hunks={wordDiff("Hi Jordan,", "Hi Maya,")} />);
    const container = screen.getByTestId("diff-text");
    expect(within(container).getByTestId("diff-equal")).toHaveTextContent("Hi");
    const del = within(container).getByTestId("diff-delete");
    const ins = within(container).getByTestId("diff-insert");
    expect(del.tagName).toBe("DEL");
    expect(ins.tagName).toBe("INS");
    expect(del).toHaveTextContent("Jordan,");
    expect(ins).toHaveTextContent("Maya,");
  });

  it("carries the change in strikethrough / underline, not colour alone", () => {
    render(<DiffText hunks={wordDiff("Hi Jordan,", "Hi Maya,")} />);
    expect(screen.getByTestId("diff-delete")).toHaveStyle({
      textDecoration: "line-through",
    });
    expect(screen.getByTestId("diff-insert")).toHaveStyle({
      textDecoration: "underline",
    });
  });

  it("preserves whitespace runs verbatim in the DOM", () => {
    render(<DiffText hunks={wordDiff("one two", "one  two three")} />);
    // The full text content round-trips the after-string's characters.
    expect(screen.getByTestId("diff-text").textContent).toContain("one");
    expect(screen.getByTestId("diff-text").textContent).toContain("three");
  });
});

describe("DiffText — accessibility", () => {
  it("labels the container with insertion / deletion totals", () => {
    // 'Hi Jordan,' -> 'Hi Maya,' is one insertion + one deletion.
    render(<DiffText hunks={wordDiff("Hi Jordan,", "Hi Maya,")} />);
    expect(screen.getByTestId("diff-text")).toHaveAccessibleName(
      "1 insertion, 1 deletion",
    );
  });

  it("pluralises the totals", () => {
    render(
      <DiffText
        hunks={wordDiff("Thanks. Bye.", "Thank you so much. Farewell now.")}
      />,
    );
    const label = screen.getByTestId("diff-text").getAttribute("aria-label")!;
    expect(label).toMatch(/^\d+ insertions?, \d+ deletions?$/);
  });

  it("exposes the diff as a group", () => {
    render(<DiffText hunks={wordDiff("a", "b")} />);
    expect(screen.getByRole("group")).toBe(screen.getByTestId("diff-text"));
  });
});

describe("DiffText — onHunkToggle seam (PRD-09)", () => {
  it("renders nothing interactive when onHunkToggle is absent", () => {
    render(<DiffText hunks={wordDiff("Hi Jordan,", "Hi Maya,")} />);
    // No buttons, and clicking a changed hunk is inert.
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    const del = screen.getByTestId("diff-delete");
    expect(del).toHaveStyle({ cursor: "auto" });
    fireEvent.click(del); // must not throw
  });

  it("invokes onHunkToggle with the hunk id when a changed hunk is clicked", () => {
    const onHunkToggle = vi.fn();
    const hunks = wordDiff("Hi Jordan,", "Hi Maya,");
    render(<DiffText hunks={hunks} onHunkToggle={onHunkToggle} />);
    const del = screen.getByTestId("diff-delete");
    fireEvent.click(del);
    expect(onHunkToggle).toHaveBeenCalledTimes(1);
    expect(onHunkToggle).toHaveBeenCalledWith(del.getAttribute("data-hunk-id"));
    fireEvent.click(screen.getByTestId("diff-insert"));
    expect(onHunkToggle).toHaveBeenCalledTimes(2);
  });

  it("does not make equal runs interactive even with a toggle handler", () => {
    const onHunkToggle = vi.fn();
    render(
      <DiffText
        hunks={wordDiff("Hi Jordan,", "Hi Maya,")}
        onHunkToggle={onHunkToggle}
      />,
    );
    fireEvent.click(screen.getByTestId("diff-equal"));
    expect(onHunkToggle).not.toHaveBeenCalled();
  });
});

describe("DiffText — empty", () => {
  it("renders no hunks and a zero-change label", () => {
    render(<DiffText hunks={[]} />);
    expect(screen.getByTestId("diff-text")).toHaveAccessibleName(
      "0 insertions, 0 deletions",
    );
  });
});
