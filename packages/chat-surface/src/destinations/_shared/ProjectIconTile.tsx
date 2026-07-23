// <ProjectIconTile> — the design `.proj-ic` project identity tile (PRD-10 D3).
//
// ONE tile, ONE ramp, ONE geometry. Before this file there were four distinct
// per-project hue-ramp formulas across seven call sites in `destinations/projects/`
// (the list card, the detail header, the ProjectEditor preview, the two Template
// tiles and the fork dialog), plus three different geometries. They all collapse
// here.
//
// The glyph is the project name's MONOGRAM (first letter, upper-cased) — NEVER
// `icon_emoji`. The server defaults every project's `icon_emoji` to 📁
// (`0043_projects.sql:39`), so rendering that field produced an identical wall of
// folders. Using the monogram fixes the desktop's missing-fallback bug by
// construction: there is nothing to fall back from. `icon_emoji` is not orphaned
// — `ProjectFilterChip` still renders it in the Library surfaces.
//
// Geometry (design `.proj-ic`, copilot.css:1698-1710): 32×32, `--radius-md` (8px),
// 13px, `--font-weight-semibold` (600), `--font-sans`. 32 is the ONLY size — the
// detail tile is NOT a size class up (design `copilot-app.jsx:404` uses the same
// `.proj-ic`).
//
// Two ramps:
//   * tinted (`colorHue` supplied) — the per-project hue. Live persists
//     `color_hue` and ships a hue picker, so the tile keeps the colour; the mock's
//     `!important` neutralisation is a leftover, not intent (PRD-10 D3). This
//     divergence is recorded via `expectDivergence` on the tile-colour anchors.
//   * neutral (`colorHue === undefined`) — `--color-surface-elevated` (#1d1d23 =
//     the design's `--panel3`) on `--color-text-strong` (#d4d4db = `--tx2`).
//     README C10: NOT the muted surface rung (#16161a = the design's `--panel2`,
//     the row/card HOVER ground — a tile painted with it would vanish on hover).
//
// Substrate-agnostic; token-driven only.

import type { CSSProperties, ReactElement } from "react";

/**
 * The ONE per-project hue ramp (PRD-10 D3). Before this, four distinct ramp
 * formulas were scattered across seven call sites. This is the single source:
 * the tile consumes it, and the legacy template / editor tiles (which keep their
 * own emoji glyph + geometry, deferred to the templates-convergence PRD in D9)
 * import it so NO `hsl(...)` literal survives outside this file — DoD 2's stated
 * rationale ("every per-project colour is produced inside ProjectIconTile.tsx").
 */
export interface ProjectHueRamp {
  readonly background: string;
  readonly border: string;
  readonly color: string;
}

export function projectHueRamp(colorHue?: number): ProjectHueRamp {
  if (colorHue === undefined) {
    return {
      // README C10: `--color-surface-elevated` (== the design's `--panel3`), NOT
      // the muted surface rung (== `--panel2`, the hover ground — a tile painted
      // with it would vanish on hover).
      background: "var(--color-surface-elevated)",
      border: "1px solid var(--color-border)",
      color: "var(--color-text-strong)",
    };
  }
  return {
    background: `hsl(${colorHue} 60% 28% / 0.45)`,
    border: `1px solid hsl(${colorHue} 60% 50% / 0.55)`,
    color: `hsl(${colorHue} 70% 82%)`,
  };
}

/**
 * The ONE per-hue picker-swatch colour (PRD-10 D3). The colour-picker dots in
 * `ProjectEditor` and `fork-from-template-dialog` show a saturated solid preview
 * of a hue (distinct from the tile's tinted ramp above), but the generative
 * formula is still per-project colour and must live here — so NO `hsl(...)`
 * literal survives outside this file (DoD 2). Both pickers import this.
 */
export function projectHueSwatchColor(colorHue: number): string {
  return `hsl(${colorHue}, 55%, 45%)`;
}

export interface ProjectIconTileProps {
  /** Project display name — its first letter is the monogram glyph. */
  readonly name: string;
  /** Per-project hue (0..359). Omit for the neutral rung. */
  readonly colorHue?: number;
  /** The only supported size is 32 (design `.proj-ic`). */
  readonly size?: 32;
  /** Optional test id override (defaults to `project-icon-tile`). */
  readonly testId?: string;
}

const baseStyle: CSSProperties = {
  width: 32,
  height: 32,
  borderRadius: "var(--radius-md)",
  display: "grid",
  placeItems: "center",
  fontSize: 13,
  fontWeight: "var(--font-weight-semibold)",
  fontFamily: "var(--font-sans)",
  flex: "none",
  boxSizing: "border-box",
};

export function ProjectIconTile({
  name,
  colorHue,
  size = 32,
  testId = "project-icon-tile",
}: ProjectIconTileProps): ReactElement {
  const initial = (name.trim()[0] ?? "?").toUpperCase();
  const ramp = projectHueRamp(colorHue);

  const style: CSSProperties = {
    ...baseStyle,
    width: size,
    height: size,
    backgroundColor: ramp.background,
    border: ramp.border,
    color: ramp.color,
  };

  return (
    <span
      style={style}
      role="img"
      aria-label={`${name} icon`}
      data-testid={testId}
      data-color-hue={colorHue ?? ""}
    >
      {initial}
    </span>
  );
}
