/**
 * Surface renderer palette — token references, not colours.
 *
 * Every entry below used to be a literal hex (`#101113` / `#181a1c` /
 * `#2a2d31` / `#f4f5f6` / `#c8ccd1` / `#9aa0a6`). That was the single reason
 * generative surfaces could not theme and read as uniformly grey: 180 call
 * sites across eleven renderers resolved to a private ladder that no theme,
 * accent, or density switch could reach. Pointing the SAME names at
 * design-system tokens re-themes all of them without touching a call site —
 * the names survive, the hardcoded ladder does not.
 *
 * Keep these as `var(--token)` strings. A renderer composes them into inline
 * styles, so a resolved colour here would re-strand the whole subtree.
 *
 * Anything that needs a HOVER, a pseudo-element, or the source-hue identity
 * register belongs in `chat-surface/src/thread-canvas/surface-language.css`
 * instead — inline styles cannot express those, and identity colour must stay
 * in one place.
 */
export const SURFACE_PALETTE = {
  /** Canvas ground behind a surface card. */
  pageBg: "var(--color-bg)",
  /** The card itself. */
  surface: "var(--color-surface)",
  /** Raised bands inside a card: table headers, toolbars, footers. */
  surfaceMute: "var(--color-surface-elevated)",
  /** Card and cell hairlines — the design's `--line2` rung. */
  border: "var(--color-border-strong)",
  /** Primary reading colour: titles, values, numbers. */
  textHi: "var(--color-text)",
  /** Body copy and secondary values. */
  textMid: "var(--color-text-strong)",
  /** Labels, metadata, column headers, anything deliberately quiet. */
  textLo: "var(--color-text-muted)",
  /**
   * The ATTENTION register. Named `lime` for continuity with the pre-v2 accent
   * (`#c2ff5a`), which no longer exists — this is the one sky accent, and it
   * marks a decision the user still owes: the live moment, an edited cell, the
   * new half of a diff, the one primary button. It is NOT a source identity;
   * that is `--surface-src` and it never routes through here.
   */
  lime: "var(--color-accent)",
  limeBgSoft: "color-mix(in srgb, var(--color-accent) 12%, transparent)",
  /**
   * The IDENTITY register, for inline-style consumers. Resolves against the
   * nearest `data-surface-hue` ancestor and degrades to a neutral when none is
   * set, so a renderer mounted outside a hue scope stays honest rather than
   * inheriting a stranger's colour.
   */
  src: "var(--surface-src, var(--color-text-subtle))",
  srcSoft:
    "color-mix(in oklch, var(--surface-src, transparent) 15%, transparent)",
  srcLine:
    "color-mix(in oklch, var(--surface-src, transparent) 55%, transparent)",
} as const;

export type SurfacePalette = typeof SURFACE_PALETTE;
