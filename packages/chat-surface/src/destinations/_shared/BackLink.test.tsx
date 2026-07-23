// BackLink tests (PRD-10 D5 / DoD 4).
//
// Pins the design `.backlink` values: mono 11px (--font-size-2xs) muted link,
// 6px gap, 14px bottom margin, and a 13×13 leading chevron svg. Also verifies
// the click contract.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { BackLink } from "./BackLink";

describe("<BackLink>", () => {
  it("renders the design values (DoD 4)", () => {
    render(<BackLink onBack={() => {}} />);
    const link = screen.getByTestId("back-link");
    expect(link.style.fontFamily).toBe("var(--font-mono)");
    expect(link.style.fontSize).toBe("var(--font-size-2xs)");
    expect(link.style.color).toBe("var(--color-text-muted)");
    expect(link.style.gap).toBe("6px");
    expect(link.style.marginBottom).toBe("14px");
  });

  it("renders a 13×13 leading chevron svg", () => {
    render(<BackLink onBack={() => {}} />);
    const svg = screen.getByTestId("back-link-chevron");
    expect(svg.getAttribute("width")).toBe("13");
    expect(svg.getAttribute("height")).toBe("13");
  });

  it("renders the default label and calls onBack on click", () => {
    const onBack = vi.fn();
    render(<BackLink onBack={onBack} />);
    const link = screen.getByTestId("back-link");
    expect(link).toHaveTextContent("All projects");
    fireEvent.click(link);
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it("accepts a custom label", () => {
    render(<BackLink onBack={() => {}} label="Back to list" />);
    expect(screen.getByTestId("back-link")).toHaveTextContent("Back to list");
  });
});
