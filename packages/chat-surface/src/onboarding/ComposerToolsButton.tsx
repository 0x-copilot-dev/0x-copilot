// ComposerToolsButton — the first-run composer "tools pill" trigger (PRD-P4).
//
// A single button that opens the `ToolsPopover`. Shows the count of tools
// currently ON (web search + active connectors — computed by the surface via
// `firstRunActiveToolCount`) as a small badge, and reflects the open state
// through `aria-expanded` so the popover reads as its controlled menu.
//
// Substrate-clean: no `window`/`document`/`fetch`; styling is design-system
// tokens only. Intended to be mounted into `AssistantComposer`'s additive
// `toolsTrigger` slot by the P4 wiring pass (NOT wired here).

import type { CSSProperties, ReactNode } from "react";

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
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-haspopup="dialog"
      aria-expanded={open}
      aria-label="Tools"
      data-testid="first-run-tools-button"
      data-open={open ? "true" : "false"}
      style={triggerStyle(open, disabled === true)}
    >
      <span aria-hidden="true" style={glyphStyle}>
        ⚙
      </span>
      <span>{COMPOSER_TOOLS_BUTTON_COPY.label}</span>
      {showBadge ? (
        <span style={badgeStyle} data-testid="first-run-tools-button-badge">
          {activeCount}
        </span>
      ) : null}
    </button>
  );
}

/* ── styles (design-system tokens only; no raw hex) ─────────────────── */

function triggerStyle(open: boolean, disabled: boolean): CSSProperties {
  return {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    background: open ? "var(--color-surface-muted)" : "transparent",
    border: "1px solid var(--color-border)",
    borderRadius: "var(--radius-full)",
    padding: "4px 10px",
    color: "var(--color-text-muted)",
    fontFamily: "var(--font-sans)",
    fontSize: "var(--font-size-xs)",
    cursor: disabled ? "default" : "pointer",
    opacity: disabled ? 0.5 : 1,
    lineHeight: 1,
  };
}

const glyphStyle: CSSProperties = {
  fontSize: "var(--font-size-sm)",
  lineHeight: 1,
};

const badgeStyle: CSSProperties = {
  minWidth: 16,
  height: 16,
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  padding: "0 4px",
  borderRadius: "var(--radius-full)",
  background: "var(--color-accent-soft)",
  color: "var(--color-accent)",
  fontSize: "var(--font-size-2xs)",
  fontWeight: "var(--font-weight-semibold)",
};
