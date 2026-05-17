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

describe("<StatusPill>", () => {
  it.each(TONES)("renders the %s tone with a labelled status", (tone) => {
    render(<StatusPill status={tone} label={`${tone} label`} />);
    const pill = screen.getByTestId("status-pill");
    expect(pill).toHaveAttribute("data-status", tone);
    expect(pill).toHaveAttribute("aria-label", `Status: ${tone} label`);
    expect(pill).toHaveTextContent(`${tone} label`);
  });

  it("renders the inline dot via aria-hidden so AT reads the label only once", () => {
    const { container } = render(<StatusPill status="ok" label="Healthy" />);
    const hidden = container.querySelectorAll('[aria-hidden="true"]');
    expect(hidden.length).toBe(1);
  });
});
