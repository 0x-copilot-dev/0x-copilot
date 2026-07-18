import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SHELL_SHORTCUTS } from "../shell/shortcuts";

import { SHORTCUTS, ShortcutsPage } from "./ShortcutsPage";

describe("<ShortcutsPage>", () => {
  it("derives its rows from the SHELL_SHORTCUTS SSOT table (FR-6.15)", () => {
    // One row per table entry, in table order, with no hand-authored copy:
    // each row is exactly { intent → id, label, chord.display → glyphs }.
    expect(SHORTCUTS).toHaveLength(SHELL_SHORTCUTS.length);
    expect(SHORTCUTS).toEqual(
      SHELL_SHORTCUTS.map((s) => ({
        id: s.intent,
        label: s.label,
        keys: Array.from(s.chord.display),
      })),
    );
  });

  it("renders every SSOT chord's label and glyphs read-only", () => {
    render(<ShortcutsPage />);
    for (const shortcut of SHELL_SHORTCUTS) {
      // Label cell, keyed by the shortcut's intent.
      expect(
        screen.getByTestId(`shortcut-${shortcut.intent}`),
      ).toHaveTextContent(shortcut.label);
      // Chord cell renders the display glyphs in press order.
      const cell = screen.getByTestId(`shortcut-keys-${shortcut.intent}`);
      const glyphs = Array.from(cell.querySelectorAll("kbd")).map(
        (k) => k.textContent,
      );
      expect(glyphs).toEqual(Array.from(shortcut.chord.display));
    }
    // Read-only reference — no interactive controls (no Record/Reset buttons).
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("renders the local-model-picker chord as ⌘⇧M glyphs in press order", () => {
    render(<ShortcutsPage />);
    const cell = screen.getByTestId("shortcut-keys-onOpenLocalModelPicker");
    const glyphs = Array.from(cell.querySelectorAll("kbd")).map(
      (k) => k.textContent,
    );
    expect(glyphs).toEqual(["⌘", "⇧", "M"]);
  });
});
