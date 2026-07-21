// Row — the design `.lrow` list row (PRD-G FR-G.1).

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Icon } from "../../icons/Icon";

import { Row } from "./Row";

describe("<Row>", () => {
  it("renders the leading icon slot, title, sub, chip, and meta", () => {
    render(
      <Row
        icon={<Icon name="clock" />}
        title="Draft reply"
        chip={<span data-testid="chip">Done</span>}
        sub="preview · gpt-4o"
        meta="2h ago"
      />,
    );
    const row = screen.getByTestId("row");
    expect(within(row).getByTestId("row-icon")).toBeInTheDocument();
    expect(within(row).getByTestId("row-title")).toHaveTextContent(
      "Draft reply",
    );
    expect(within(row).getByTestId("row-sub")).toHaveTextContent(
      "preview · gpt-4o",
    );
    expect(within(row).getByTestId("row-chip")).toHaveTextContent("Done");
    expect(within(row).getByTestId("row-meta")).toHaveTextContent("2h ago");
  });

  it("uses 12.5px (xs) title + 11px (2xs) subtle sub, body font (not mono)", () => {
    render(<Row title="T" sub="s" />);
    expect(screen.getByTestId("row-title").style.fontSize).toBe(
      "var(--font-size-xs)",
    );
    const sub = screen.getByTestId("row-sub");
    expect(sub.style.fontSize).toBe("var(--font-size-2xs)");
    expect(sub.style.color).toBe("var(--color-text-subtle)");
    // sub-line is BODY font — it must NOT carry the mono family.
    expect(sub.style.fontFamily).toBe("");
  });

  it("renders meta in the mono font", () => {
    render(<Row title="T" meta="2h" />);
    expect(screen.getByTestId("row-meta").style.fontFamily).toBe(
      "var(--font-mono)",
    );
  });

  it("omits optional slots when not provided (title-only row)", () => {
    render(<Row title="Only title" />);
    expect(screen.queryByTestId("row-icon")).toBeNull();
    expect(screen.queryByTestId("row-sub")).toBeNull();
    expect(screen.queryByTestId("row-chip")).toBeNull();
    expect(screen.queryByTestId("row-meta")).toBeNull();
  });

  it("as a button: fires onActivate on click and on Enter/Space", () => {
    const onActivate = vi.fn();
    render(<Row title="Open me" onActivate={onActivate} ariaLabel="Open me" />);
    const row = screen.getByTestId("row");
    expect(row).toHaveAttribute("role", "button");
    expect(row).toHaveAttribute("tabindex", "0");
    expect(row).toHaveAttribute("aria-label", "Open me");

    fireEvent.click(row);
    expect(onActivate).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(row, { key: "Enter" });
    expect(onActivate).toHaveBeenCalledTimes(2);
    fireEvent.keyDown(row, { key: " " });
    expect(onActivate).toHaveBeenCalledTimes(3);
  });

  it("does not activate on an unrelated key", () => {
    const onActivate = vi.fn();
    render(<Row title="X" onActivate={onActivate} />);
    fireEvent.keyDown(screen.getByTestId("row"), { key: "a" });
    expect(onActivate).not.toHaveBeenCalled();
  });

  it("inert mode has no role/tabindex/handlers", () => {
    render(<Row title="Static" />);
    const row = screen.getByTestId("row");
    expect(row).not.toHaveAttribute("role");
    expect(row).not.toHaveAttribute("tabindex");
  });
});
