// <ShortcutsPage /> — Settings → Account → Shortcuts (DESIGN-SPEC §4/§6, PR-5.3).
//
// A READ-ONLY reference grid of the keyboard shortcut set. Phase 5 only renders
// it; the chords are actually registered/executed in Phase 6B (FR-5.10 non-goal
// note). So this page has no recording, no overrides, no persistence — it is a
// pure, static reference. The `SHORTCUTS` list is exported so the Phase-6A
// command palette / 6B keymap can register against the same SSOT.
//
// Substrate-agnostic: no `navigator`/`window` platform sniffing. The product is
// macOS-first (DESIGN-SPEC: "System — Match macOS", "macOS Keychain"), so the
// glyphs are the mac chord glyphs exactly as DESIGN-SPEC §6 writes them.
//
// Colors resolve ONLY to design-system v2 tokens.

import { type CSSProperties, type ReactElement } from "react";

import { SetCard } from "./SettingsChrome";

export interface ShortcutRow {
  /** Stable id (shared with the Phase-6B keymap registry). */
  readonly id: string;
  readonly label: string;
  /** The chord glyphs, in press order (e.g. ["⌘", "⇧", "M"]). */
  readonly keys: readonly string[];
}

// DESIGN-SPEC §6, in the spec's order. The glyphs are the display form; the
// Phase-6B keymap owns the tinykeys/electron chord strings keyed by `id`.
export const SHORTCUTS: readonly ShortcutRow[] = [
  { id: "run.new", label: "New run", keys: ["⌘", "N"] },
  { id: "palette.open", label: "Command palette", keys: ["⌘", "K"] },
  { id: "approval.approve", label: "Approve action", keys: ["⌘", "↵"] },
  { id: "approval.reject", label: "Reject action", keys: ["⌘", "⌫"] },
  { id: "run.pause", label: "Pause run", keys: ["⌘", "."] },
  { id: "timeline.rewind", label: "Rewind timeline", keys: ["⌘", "←"] },
  { id: "timeline.step", label: "Step forward", keys: ["⌘", "→"] },
  { id: "timeline.live", label: "Jump to live", keys: ["⌘", "L"] },
  { id: "run.mode", label: "Switch mode", keys: ["⌘", "M"] },
  {
    id: "models.localPicker",
    label: "Local model picker",
    keys: ["⌘", "⇧", "M"],
  },
  { id: "settings.open", label: "Settings", keys: ["⌘", ","] },
  { id: "activity.search", label: "Search activity", keys: ["⌘", "⇧", "F"] },
];

// ---------------------------------------------------------------------------
// Styles (token-only)
// ---------------------------------------------------------------------------

const listStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "1fr auto",
  alignItems: "center",
  columnGap: "var(--space-lg)",
  rowGap: 0,
  margin: 0,
};

const labelCellStyle: CSSProperties = {
  margin: 0,
  padding: "var(--space-sm) 0",
  fontSize: "var(--font-size-sm)",
  color: "var(--color-text)",
  borderBottom: "1px solid var(--color-border)",
};

const chordCellStyle: CSSProperties = {
  margin: 0,
  padding: "var(--space-sm) 0",
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
  justifyContent: "flex-end",
  borderBottom: "1px solid var(--color-border)",
};

const kbdStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  minWidth: 22,
  height: 22,
  padding: "0 6px",
  borderRadius: "var(--radius-sm)",
  border: "1px solid var(--color-border-strong)",
  backgroundColor: "var(--color-surface-muted)",
  color: "var(--color-text-muted)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-2xs)",
  lineHeight: 1,
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function ShortcutsPage(): ReactElement {
  return (
    <SetCard
      title="Shortcuts"
      meta="Keyboard shortcuts for the run cockpit, palette, and approvals."
      data-testid="shortcuts-page"
    >
      <dl style={listStyle}>
        {SHORTCUTS.map((shortcut) => (
          <div key={shortcut.id} style={{ display: "contents" }}>
            <dt style={labelCellStyle} data-testid={`shortcut-${shortcut.id}`}>
              {shortcut.label}
            </dt>
            <dd
              style={chordCellStyle}
              aria-label={shortcut.keys.join(" ")}
              data-testid={`shortcut-keys-${shortcut.id}`}
            >
              {shortcut.keys.map((key, index) => (
                <kbd key={`${shortcut.id}-${index}`} style={kbdStyle}>
                  {key}
                </kbd>
              ))}
            </dd>
          </div>
        ))}
      </dl>
    </SetCard>
  );
}
