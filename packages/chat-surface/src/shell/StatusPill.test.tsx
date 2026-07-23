import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusPill, type StatusTone } from "./StatusPill";

const TONES: ReadonlyArray<StatusTone> = [
  "ok",
  "error",
  "warning",
  "info",
  "muted",
];

const RECIPE_CLASS: Readonly<Record<StatusTone, string>> = {
  ok: "ui-badge ui-badge--success",
  error: "ui-badge ui-badge--danger",
  warning: "ui-badge ui-badge--warning",
  info: "ui-badge ui-badge--accent",
  muted: "ui-badge ui-badge--muted",
};

describe("<StatusPill>", () => {
  it.each(TONES)("renders the %s tone with a labelled status", (tone) => {
    render(<StatusPill status={tone} label={`${tone} label`} />);
    const pill = screen.getByTestId("status-pill");
    expect(pill).toHaveAttribute("data-status", tone);
    expect(pill).toHaveAttribute("aria-label", `Status: ${tone} label`);
    expect(pill).toHaveTextContent(`${tone} label`);
  });

  it("applies the design-system .ui-badge recipe (the design's outlined .chip), not an inline style object", () => {
    const { container } = render(<StatusPill status="ok" label="running" />);
    const pill = container.querySelector('[data-testid="status-pill"]')!;
    // DoD-6: the tone maps to the recipe classes exactly — no bespoke style.
    expect(pill.className).toBe("ui-badge ui-badge--success");
    // No inline style carries a fill / colour: the recipe owns all of it.
    expect(pill.getAttribute("style")).toBeNull();
  });

  it.each(TONES)("maps %s to its .ui-badge recipe class", (tone) => {
    const { container } = render(<StatusPill status={tone} label="x" />);
    expect(
      container.querySelector('[data-testid="status-pill"]')!.className,
    ).toBe(RECIPE_CLASS[tone]);
  });

  it("appends a caller className after the recipe classes", () => {
    const { container } = render(
      <StatusPill status="warning" label="x" className="extra" />,
    );
    expect(
      container.querySelector('[data-testid="status-pill"]')!.className,
    ).toBe("ui-badge ui-badge--warning extra");
  });

  it("renders NO dot by default — the design draws the dot only on the live chip", () => {
    const { container } = render(<StatusPill status="ok" label="running" />);
    // DoD-7: dot off by default (this fails on `main`, where showDot defaults true).
    expect(container.querySelectorAll(".ui-badge__dot").length).toBe(0);
    expect(container.querySelectorAll('[aria-hidden="true"]').length).toBe(0);
  });

  it("renders exactly one aria-hidden dot when showDot is set", () => {
    const { container } = render(
      <StatusPill status="ok" label="running" showDot />,
    );
    expect(container.querySelectorAll(".ui-badge__dot").length).toBe(1);
    // The dot is aria-hidden so AT reads the label only once; it is the first child.
    const pill = container.querySelector('[data-testid="status-pill"]')!;
    expect(pill.querySelectorAll('[aria-hidden="true"]').length).toBe(1);
    expect(
      (pill.firstElementChild as HTMLElement).className.includes(
        "ui-badge__dot",
      ),
    ).toBe(true);
  });
});
