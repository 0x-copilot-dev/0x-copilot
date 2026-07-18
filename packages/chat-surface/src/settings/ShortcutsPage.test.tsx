import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SHORTCUTS, ShortcutsPage } from "./ShortcutsPage";

describe("<ShortcutsPage>", () => {
  it("renders all 12 DESIGN-SPEC §6 shortcuts read-only", () => {
    render(<ShortcutsPage />);
    expect(SHORTCUTS).toHaveLength(12);
    for (const shortcut of SHORTCUTS) {
      expect(screen.getByTestId(`shortcut-${shortcut.id}`)).toHaveTextContent(
        shortcut.label,
      );
    }
    // Read-only reference — no interactive controls (no Record/Reset buttons).
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("renders each chord as kbd glyphs in press order", () => {
    render(<ShortcutsPage />);
    const localPicker = screen.getByTestId("shortcut-keys-models.localPicker");
    const glyphs = Array.from(localPicker.querySelectorAll("kbd")).map(
      (k) => k.textContent,
    );
    expect(glyphs).toEqual(["⌘", "⇧", "M"]);
  });

  it("covers the canonical chords from §6", () => {
    const byId = new Map(SHORTCUTS.map((s) => [s.id, s.keys.join(" ")]));
    expect(byId.get("run.new")).toBe("⌘ N");
    expect(byId.get("palette.open")).toBe("⌘ K");
    expect(byId.get("approval.approve")).toBe("⌘ ↵");
    expect(byId.get("approval.reject")).toBe("⌘ ⌫");
    expect(byId.get("settings.open")).toBe("⌘ ,");
    expect(byId.get("activity.search")).toBe("⌘ ⇧ F");
  });
});
