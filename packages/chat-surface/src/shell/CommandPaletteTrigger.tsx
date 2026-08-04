// CommandPaletteTrigger — small topbar button that opens the palette.
//
// Source: team-memory-cmdk-prd.md §7.3 (palette placement).
//
// Renders "Search… ⌘K" — the host wires it wherever it likes (topbar,
// home hero, etc). The keyboard shortcut hint defaults to ⌘K on Apple
// platforms, Ctrl+K elsewhere; substrate hosts can override via `hint`.

import { type CSSProperties, type ReactElement } from "react";

import { Icon } from "../icons/Icon";

export interface CommandPaletteTriggerProps {
  readonly onOpen: () => void;
  /** Optional override; defaults to "⌘K" on Apple, "Ctrl+K" elsewhere. */
  readonly hint?: string;
  /** Defaults to "Search & commands" (design `.tb-search`). */
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

// The trigger is SHRINKABLE by default. It used to declare `minWidth: 200`
// inline, which is a floor no call site could lower (an inline declaration beats
// any class rule short of `!important`), so in a flex row the button stayed
// 200px wide while everything around it gave ground — and once the row ran out
// the surplus was clipped rather than reflowed. `minWidth: 0` keeps the natural
// size as the PREFERRED size and lets the label ellipsize; the icon and the ⌘K
// hint are `flex: none`, so what is left when the label is gone is exactly the
// affordance a user still needs to recognise it.
//
// Note what is deliberately ABSENT: no `flex` shorthand. The initial value is
// already `0 1 auto`, so restating it inline would buy nothing and would cost
// the one thing that matters — a call site's ability to set a flex basis from a
// class (`Topbar` asks for 250px that way). Inline beats class, so anything
// declared here is a decision taken away from every host.
const triggerStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  minWidth: 0,
  maxWidth: "100%",
  overflow: "hidden",
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
  flex: "none",
  fontSize: "var(--font-size-xs, 11px)",
  color: "var(--color-text-subtle, #7e7e84)",
  border: "1px solid var(--color-border, #2a2a2c)",
  borderRadius: "var(--radius-sm, 4px)",
  padding: "1px 6px",
};

const labelRowStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 8,
  flex: "0 1 auto",
  minWidth: 0,
  overflow: "hidden",
};

const labelTextStyle: CSSProperties = {
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

export function CommandPaletteTrigger({
  onOpen,
  hint,
  label = "Search & commands",
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
      <span style={labelRowStyle}>
        <Icon
          name="search"
          size={13}
          style={{ color: "var(--color-text-subtle)", flex: "none" }}
        />
        <span style={labelTextStyle}>{label}</span>
      </span>
      <span style={hintStyle} aria-hidden="true">
        {resolvedHint}
      </span>
    </button>
  );
}
