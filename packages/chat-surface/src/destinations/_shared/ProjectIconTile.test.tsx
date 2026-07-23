// ProjectIconTile tests (PRD-10 D3 / DoD 3 + DoD 10).
//
// Pins the design geometry numerically, the monogram-not-emoji glyph rule, and
// README C10's neutral-rung token mapping (--color-surface-elevated /
// --color-text-strong, NOT --color-surface-muted).

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProjectIconTile } from "./ProjectIconTile";

describe("<ProjectIconTile>", () => {
  it("renders the design geometry numerically (DoD 3)", () => {
    render(<ProjectIconTile name="Launch Week" colorHue={200} />);
    const tile = screen.getByTestId("project-icon-tile");
    // React serialises numeric width/height/fontSize to px strings.
    expect(tile.style.width).toBe("32px");
    expect(tile.style.height).toBe("32px");
    expect(tile.style.borderRadius).toBe("var(--radius-md)");
    expect(tile.style.fontSize).toBe("13px");
    expect(tile.style.fontWeight).toBe("var(--font-weight-semibold)");
  });

  it("renders the name's first letter, upper-cased, NEVER icon_emoji", () => {
    // The tile takes no `icon_emoji` — the glyph is always the monogram, which
    // is exactly how the desktop emoji-wall bug is fixed by construction.
    render(<ProjectIconTile name="Launch Week" />);
    expect(screen.getByTestId("project-icon-tile").textContent).toBe("L");
  });

  it("falls back to '?' for an empty/whitespace name", () => {
    render(<ProjectIconTile name="   " />);
    expect(screen.getByTestId("project-icon-tile").textContent).toBe("?");
  });

  it("uses the neutral rung tokens when no colorHue is supplied (README C10)", () => {
    render(<ProjectIconTile name="Zenith" />);
    const tile = screen.getByTestId("project-icon-tile");
    // --color-surface-elevated (== the design's --panel3), NOT
    // --color-surface-muted (== --panel2, the hover ground).
    expect(tile.style.backgroundColor).toBe("var(--color-surface-elevated)");
    expect(tile.style.color).toBe("var(--color-text-strong)");
  });

  it("produces a per-project hue ramp when colorHue is supplied", () => {
    // jsdom normalises hsl(...) to rgba(...), so assert on the computed rgba the
    // design ramp resolves to rather than the source string.
    render(<ProjectIconTile name="Zenith" colorHue={140} />);
    const tile = screen.getByTestId("project-icon-tile");
    expect(tile).toHaveAttribute("data-color-hue", "140");
    expect(tile.style.backgroundColor).toBe("rgba(29, 114, 57, 0.45)");
    // A tinted tile is NOT the neutral rung.
    expect(tile.style.backgroundColor).not.toBe(
      "var(--color-surface-elevated)",
    );
  });
});
