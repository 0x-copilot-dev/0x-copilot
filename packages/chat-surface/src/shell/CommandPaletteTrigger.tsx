// CommandPaletteTrigger — small topbar button that opens the palette.
//
// Source: team-memory-cmdk-prd.md §7.3 (palette placement).
//
// Renders "Search… ⌘K" — the host wires it wherever it likes (topbar,
// home hero, etc). The keyboard shortcut hint defaults to ⌘K on Apple
// platforms, Ctrl+K elsewhere; substrate hosts can override via `hint`.

import { type CSSProperties, type ReactElement } from "react";

export interface CommandPaletteTriggerProps {
  readonly onOpen: () => void;
  /** Optional override; defaults to "⌘K" on Apple, "Ctrl+K" elsewhere. */
  readonly hint?: string;
  readonly label?: string;
  readonly className?: string;
}

function defaultHint(): string {
  const nav = (
    globalThis as { navigator?: { platform?: string; userAgent?: string } }
  ).navigator;
  const probe = `${nav?.platform ?? ""} ${nav?.userAgent ?? ""}`.toLowerCase();
  const isApple = /mac|iphone|ipad|ipod/.test(probe);
  return isApple ? "⌘K" : "Ctrl+K";
}

const triggerStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  minWidth: 200,
  height: 28,
  padding: "0 10px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border, #2a2a2c)",
  background: "var(--color-surface-muted, #1f1f1f)",
  color: "var(--color-text-muted, #b4b4b8)",
  fontSize: "var(--font-size-sm, 13px)",
  cursor: "pointer",
};

const hintStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 11px)",
  color: "var(--color-text-subtle, #7e7e84)",
  border: "1px solid var(--color-border, #2a2a2c)",
  borderRadius: "var(--radius-sm, 4px)",
  padding: "1px 6px",
};

export function CommandPaletteTrigger({
  onOpen,
  hint,
  label = "Search…",
  className,
}: CommandPaletteTriggerProps): ReactElement {
  const resolvedHint = hint ?? defaultHint();
  return (
    <button
      type="button"
      onClick={onOpen}
      className={className}
      style={triggerStyle}
      aria-label="Open command palette"
      data-testid="command-palette-trigger"
    >
      <span>{label}</span>
      <span style={hintStyle} aria-hidden="true">
        {resolvedHint}
      </span>
    </button>
  );
}
