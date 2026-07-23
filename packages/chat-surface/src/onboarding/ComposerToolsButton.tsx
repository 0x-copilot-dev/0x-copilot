// ComposerToolsButton — the first-run composer "tools pill" trigger (PRD-P4).
//
// A single button that opens the `ToolsPopover`. Shows the count of tools
// currently ON (web search + active connectors — computed by the surface via
// `firstRunActiveToolCount`) next to the label, and reflects the open state
// through `aria-expanded` so the popover reads as its controlled menu.
//
// Design parity (composer punch-list rows 10–12): this used to be a fully
// inline-styled 12.48px sans capsule with a permanent border, a "⚙" text glyph
// and an accent-filled 999px count badge. It now renders the shared
// `.ui-cpill` recipe from `@0x-copilot/design-system` (mono 10px/400, 26px tall,
// 7px radius, transparent border that only appears on hover/open, quiet panel
// fill when open — NOT an accent ring), the canonical `plug` glyph from the
// icon SSOT (`<Icon name="plug"/>`; `.ui-cpill svg` sizes it to 11x11), and the
// count as plain dimmed mono text via `.ui-cpill__n`.
//
// Owner ruling kept deliberately: the count is the ACTIVE count only ("1"), not
// the design's "on/total" — the denominator is noise once the popover lists
// every tool. The badge stays hidden at 0.
//
// Substrate-clean: no `window`/`document`/`fetch`; styling is design-system
// recipes/tokens only. Intended to be mounted into `AssistantComposer`'s
// additive `toolsTrigger` slot by the P4 wiring pass (NOT wired here).

import type { CSSProperties, ReactNode } from "react";

import { Icon } from "../icons/Icon";

export const COMPOSER_TOOLS_BUTTON_COPY = {
  label: "Tools",
} as const;

export interface ComposerToolsButtonProps {
  readonly open: boolean;
  readonly onClick: () => void;
  /** Tools currently ON (web search + active connectors). Badge hidden at 0. */
  readonly activeCount: number;
  readonly disabled?: boolean;
}

export function ComposerToolsButton(
  props: ComposerToolsButtonProps,
): ReactNode {
  const { open, onClick, activeCount, disabled } = props;
  const showBadge = activeCount > 0;
  // The recipe's open state is keyed off `.ui-cpill[data-open]` — a PRESENCE
  // selector, exactly as the design authors it (`data-open={open || undefined}`).
  // Rendering `data-open="false"` when closed would therefore pin the pill in
  // its open fill forever, so the attribute is omitted rather than stringified.
  // `aria-expanded` (value-matched) carries the closed state for a11y + tests.
  return (
    <button
      type="button"
      className="ui-cpill"
      onClick={onClick}
      disabled={disabled}
      aria-haspopup="dialog"
      aria-expanded={open}
      aria-label="Tools"
      data-testid="first-run-tools-button"
      data-open={open ? "true" : undefined}
      style={disabled === true ? disabledStyle : undefined}
    >
      <Icon name="plug" size={11} />
      <span className="ui-cpill__lb">{COMPOSER_TOOLS_BUTTON_COPY.label}</span>
      {showBadge ? (
        <span
          className="ui-cpill__n"
          data-testid="first-run-tools-button-badge"
        >
          {activeCount}
        </span>
      ) : null}
    </button>
  );
}

/* ── styles ──────────────────────────────────────────────────────────────
 * Everything visual lives in the `.ui-cpill` recipe. The ONE thing the recipe
 * does not cover is the disabled affordance (`.ui-cpill` has no `:disabled`
 * rule, unlike `.ui-csend`), so the dimmed/no-pointer treatment stays here
 * rather than being silently dropped. No sizing, type, colour or border is
 * authored at this call site.
 */
const disabledStyle: CSSProperties = {
  opacity: 0.5,
  cursor: "default",
};
