// Shell width classes — the single source of truth for the responsive scale.
//
// Source: docs/plan/windowed-mode/PRD-00-overview.md §4.1.
//
// Why container widths and not `@media`: this package is substrate-agnostic and
// its eslint boundary bans `window` / `matchMedia`, but more importantly the
// VIEWPORT IS THE WRONG SIGNAL. The cockpit renders into a grid cell whose width
// depends on the app rail, an open context panel, the right rail, and the Studio
// rail split. A viewport query would tell a component the window is 1400px wide
// while it is being painted into a 380px column.
//
// The pattern is not new here — `destinations/inbox/useInboxLayout.tsx` already
// observes its own container with `ResizeObserver` for exactly this reason. This
// file generalises the breakpoint half of that idea to the shell.
//
// FR-0.1: no component may hard-code 720 or 1120. Import from here.

/**
 * Container-width thresholds, in CSS pixels.
 *
 * - `compact` is the width BELOW which we drop to one column and turn side
 *   panels into overlays. 720 = `48 (rail) + 224 (panel) + 448 (canvas)`; below
 *   it a 224px dock is taking a third of the surface and must stop being a dock.
 * - `regular` is the width AT/ABOVE which today's full layout applies unchanged.
 *   1120 = `48 + 224 + 848`, the point at which the transcript still reads
 *   comfortably alongside a docked panel.
 */
export const SHELL_BREAKPOINTS = {
  compact: 720,
  regular: 1120,
} as const;

/**
 * How much horizontal room the surface has.
 *
 * `wide` MUST render byte-identically to the pre-responsive build — this scale
 * only adds two narrower behaviours, it changes nothing at full screen (FR-0.5).
 */
export type ShellWidthClass = "compact" | "regular" | "wide";

/**
 * SSR / pre-observer default. Deliberately the widest class so the first paint
 * is the full layout and narrowing is a single opt-in transition, rather than a
 * flash of compact chrome that then expands. Mirrors `useInboxLayout`'s
 * `defaultWidthPx` stance.
 */
export const DEFAULT_SHELL_WIDTH_CLASS: ShellWidthClass = "wide";

/** Width in CSS pixels → the class that width resolves to. */
export function widthClassFor(px: number): ShellWidthClass {
  // A non-finite / non-positive measurement means "we have not measured yet"
  // (a detached node, a display:none ancestor, the first frame). Falling back
  // to the default keeps that indistinguishable from pre-observer state instead
  // of momentarily claiming `compact`.
  if (!Number.isFinite(px) || px <= 0) {
    return DEFAULT_SHELL_WIDTH_CLASS;
  }
  if (px < SHELL_BREAKPOINTS.compact) {
    return "compact";
  }
  if (px < SHELL_BREAKPOINTS.regular) {
    return "regular";
  }
  return "wide";
}
