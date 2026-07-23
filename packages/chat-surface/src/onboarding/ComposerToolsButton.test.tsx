// ComposerToolsButton — the composer tools pill trigger (PRD-P4).
//
// Behaviour + design-parity contract (composer punch-list rows 10–12): the pill
// renders the shared `.ui-cpill` recipe, the canonical `plug` icon (never a "⚙"
// text glyph), and the active count as plain `.ui-cpill__n` text rather than an
// accent-filled badge. The recipe owns every visual declaration, so the test
// guards "no inline styling here" instead of asserting pixel values twice.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ComposerToolsButton } from "./ComposerToolsButton";

describe("<ComposerToolsButton>", () => {
  it("renders the label and reflects the open state via aria-expanded", () => {
    render(<ComposerToolsButton open onClick={vi.fn()} activeCount={0} />);
    const btn = screen.getByTestId("first-run-tools-button");
    expect(btn.textContent).toContain("Tools");
    expect(btn.getAttribute("aria-expanded")).toBe("true");
    expect(btn.getAttribute("data-open")).toBe("true");
  });

  it("hides the badge at zero active tools", () => {
    render(
      <ComposerToolsButton open={false} onClick={vi.fn()} activeCount={0} />,
    );
    expect(screen.queryByTestId("first-run-tools-button-badge")).toBeNull();
    expect(
      screen
        .getByTestId("first-run-tools-button")
        .getAttribute("aria-expanded"),
    ).toBe("false");
  });

  it("omits data-open when closed so the presence selector stays off", () => {
    // `.ui-cpill[data-open]` matches on PRESENCE — a stringified "false" would
    // pin the pill in its open fill. Closed must mean "no attribute".
    render(
      <ComposerToolsButton open={false} onClick={vi.fn()} activeCount={0} />,
    );
    expect(
      screen.getByTestId("first-run-tools-button").hasAttribute("data-open"),
    ).toBe(false);
  });

  it("shows the active count in the badge", () => {
    render(
      <ComposerToolsButton open={false} onClick={vi.fn()} activeCount={3} />,
    );
    expect(screen.getByTestId("first-run-tools-button-badge").textContent).toBe(
      "3",
    );
  });

  it("fires onClick when enabled", () => {
    const onClick = vi.fn();
    render(
      <ComposerToolsButton open={false} onClick={onClick} activeCount={1} />,
    );
    fireEvent.click(screen.getByTestId("first-run-tools-button"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("does not fire onClick when disabled", () => {
    const onClick = vi.fn();
    render(
      <ComposerToolsButton
        open={false}
        onClick={onClick}
        activeCount={1}
        disabled
      />,
    );
    fireEvent.click(screen.getByTestId("first-run-tools-button"));
    expect(onClick).not.toHaveBeenCalled();
  });

  /* ── design parity (rows 10–12) ──────────────────────────────────────── */

  it("wears the shared .ui-cpill recipe instead of inline pill styling", () => {
    render(
      <ComposerToolsButton open={false} onClick={vi.fn()} activeCount={0} />,
    );
    const btn = screen.getByTestId("first-run-tools-button");
    expect(btn.classList.contains("ui-cpill")).toBe(true);
    // The recipe owns type / radius / border / padding — none of it is
    // re-authored inline (the old build set all four here).
    const inline = btn.getAttribute("style");
    expect(inline === null || inline === "").toBe(true);
  });

  it("renders the plug icon, not a gear text glyph", () => {
    const { container } = render(
      <ComposerToolsButton open={false} onClick={vi.fn()} activeCount={0} />,
    );
    const btn = screen.getByTestId("first-run-tools-button");
    expect(btn.textContent).not.toContain("⚙");
    const svg = container.querySelector(
      "[data-testid='first-run-tools-button'] svg",
    );
    expect(svg).not.toBeNull();
    expect(svg?.getAttribute("width")).toBe("11");
    expect(svg?.getAttribute("height")).toBe("11");
    // The `plug` glyph from the icon SSOT (`ICON_PATHS.plug`).
    expect(svg?.querySelector("path")?.getAttribute("d")).toBe(
      "M9 3v6M15 3v6M6 9h12v3a6 6 0 0 1-12 0z M12 18v3",
    );
    expect(svg?.getAttribute("aria-hidden")).toBe("true");
  });

  it("renders the count as flat .ui-cpill__n text, not an accent badge", () => {
    render(
      <ComposerToolsButton open={false} onClick={vi.fn()} activeCount={2} />,
    );
    const badge = screen.getByTestId("first-run-tools-button-badge");
    expect(badge.classList.contains("ui-cpill__n")).toBe(true);
    const inline = badge.getAttribute("style");
    expect(inline === null || inline === "").toBe(true);
  });

  it("keeps the dimmed affordance the recipe has no :disabled rule for", () => {
    render(
      <ComposerToolsButton
        open={false}
        onClick={vi.fn()}
        activeCount={1}
        disabled
      />,
    );
    const btn = screen.getByTestId("first-run-tools-button");
    expect(btn.classList.contains("ui-cpill")).toBe(true);
    expect(btn.style.opacity).toBe("0.5");
    expect(btn.style.cursor).toBe("default");
  });
});
