// <ShortcutsPage /> — Settings → Account → Shortcuts (DESIGN-SPEC §4/§6, PR-5.3).
//
// A READ-ONLY reference grid of the keyboard shortcut set. This page only
// renders the chords; they are registered/executed by `useShellShortcuts`
// (Phase 6). So this page has no recording, no overrides, no persistence — it
// is a pure, static reference.
//
// FR-6.15 (SSOT): the rows are DERIVED from the one shortcut table in
// `shell/shortcuts.ts` (`SHELL_SHORTCUTS`) — the SAME table `useShellShortcuts`
// wires — so the displayed list cannot drift from the wired chords. There is no
// second hand-authored copy here: each row's `label` and glyphs come straight
// from the table's `label` and `chord.display`.
//
// Substrate-agnostic: no `navigator`/`window` platform sniffing. The product is
// macOS-first (DESIGN-SPEC: "System — Match macOS", "macOS Keychain"), so the
// glyphs are the mac chord glyphs exactly as DESIGN-SPEC §6 writes them (the
// table's `chord.display`, split into per-key glyphs for rendering).
//
// Colors resolve ONLY to design-system v2 tokens.

import { type CSSProperties, type ReactElement } from "react";

import { SHELL_SHORTCUTS } from "../shell/shortcuts";

import { SetCard } from "./SettingsChrome";

export interface ShortcutRow {
  /** Stable id — the shortcut's `intent` from the SSOT table. */
  readonly id: string;
  readonly label: string;
  /** The chord glyphs, in press order (e.g. ["⌘", "⇧", "M"]). */
  readonly keys: readonly string[];
}

// Derived from the SSOT table (`shell/shortcuts.ts`) in its canonical order:
// the five global chords first, then the seven run-scoped chords. Each row maps
// `intent` → id, keeps `label`, and splits `chord.display` (e.g. "⌘⇧M") into
// per-key glyphs (["⌘", "⇧", "M"]) for the read-only grid.
export const SHORTCUTS: readonly ShortcutRow[] = SHELL_SHORTCUTS.map(
  (shortcut) => ({
    id: shortcut.intent,
    label: shortcut.label,
    keys: Array.from(shortcut.chord.display),
  }),
);

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
