// shortcuts.ts — SSOT table + matcher tests.

import { describe, expect, it } from "vitest";

import {
  SHELL_SHORTCUTS,
  matchesChord,
  normalizeShortcutKey,
  type ShellShortcut,
  type ShortcutChord,
  type ShortcutIntent,
  type ShortcutKeyEvent,
} from "./shortcuts";

/** The DESIGN-SPEC.md §6 chord set, exactly (FR-6.10). */
const EXPECTED_INTENTS: readonly ShortcutIntent[] = [
  "onNewRun",
  "onOpenPalette",
  "onOpenSettings",
  "onOpenLocalModelPicker",
  "onSearchActivity",
  "onSwitchMode",
  "onRewind",
  "onStepForward",
  "onJumpLive",
  "onPauseRun",
  "onApprove",
  "onReject",
];

/**
 * Build a synthetic key event. Defaults to the command modifier held and no
 * other modifiers; override any field via `over`.
 */
function evt(
  over: Partial<ShortcutKeyEvent> & { key: string },
): ShortcutKeyEvent {
  return {
    metaKey: true,
    ctrlKey: false,
    shiftKey: false,
    altKey: false,
    ...over,
  };
}

/** Build a synthetic key event that matches `chord` exactly. */
function eventFor(chord: ShortcutChord): ShortcutKeyEvent {
  return evt({ key: chord.key, shiftKey: chord.shift });
}

function shortcutFor(intent: ShortcutIntent): ShellShortcut {
  const found = SHELL_SHORTCUTS.find((s) => s.intent === intent);
  if (found === undefined) {
    throw new Error(`missing shortcut for intent ${intent}`);
  }
  return found;
}

describe("SHELL_SHORTCUTS table (SSOT)", () => {
  it("contains exactly the 12 DESIGN-SPEC §6 chords", () => {
    expect(SHELL_SHORTCUTS).toHaveLength(12);
    expect(SHELL_SHORTCUTS.map((s) => s.intent)).toEqual(EXPECTED_INTENTS);
  });

  it("has a unique intent for every row", () => {
    const intents = SHELL_SHORTCUTS.map((s) => s.intent);
    expect(new Set(intents).size).toBe(intents.length);
  });

  it("has a unique display string and (key, shift) pair for every row", () => {
    const displays = SHELL_SHORTCUTS.map((s) => s.chord.display);
    expect(new Set(displays).size).toBe(displays.length);

    const keyPairs = SHELL_SHORTCUTS.map(
      (s) => `${s.chord.key}:${s.chord.shift}`,
    );
    expect(new Set(keyPairs).size).toBe(keyPairs.length);
  });

  it("marks only ⌘K and ⌘, as input-safe (FR-6.11)", () => {
    const inputSafe = SHELL_SHORTCUTS.filter((s) => s.inputSafe).map(
      (s) => s.intent,
    );
    expect(inputSafe).toEqual(["onOpenPalette", "onOpenSettings"]);
  });

  it("scopes the five global chords and seven run chords correctly (FR-6.13)", () => {
    const global = SHELL_SHORTCUTS.filter((s) => s.scope === "global").map(
      (s) => s.intent,
    );
    const run = SHELL_SHORTCUTS.filter((s) => s.scope === "run").map(
      (s) => s.intent,
    );
    expect(global).toEqual([
      "onNewRun",
      "onOpenPalette",
      "onOpenSettings",
      "onOpenLocalModelPicker",
      "onSearchActivity",
    ]);
    expect(run).toEqual([
      "onSwitchMode",
      "onRewind",
      "onStepForward",
      "onJumpLive",
      "onPauseRun",
      "onApprove",
      "onReject",
    ]);
  });

  it("carries the expected display glyphs and labels", () => {
    expect(shortcutFor("onNewRun").chord.display).toBe("⌘N");
    expect(shortcutFor("onOpenPalette").chord.display).toBe("⌘K");
    expect(shortcutFor("onOpenSettings").chord.display).toBe("⌘,");
    expect(shortcutFor("onOpenLocalModelPicker").chord.display).toBe("⌘⇧M");
    expect(shortcutFor("onSearchActivity").chord.display).toBe("⌘⇧F");
    expect(shortcutFor("onSwitchMode").chord.display).toBe("⌘M");
    expect(shortcutFor("onRewind").chord.display).toBe("⌘←");
    expect(shortcutFor("onStepForward").chord.display).toBe("⌘→");
    expect(shortcutFor("onJumpLive").chord.display).toBe("⌘L");
    expect(shortcutFor("onPauseRun").chord.display).toBe("⌘.");
    expect(shortcutFor("onApprove").chord.display).toBe("⌘↵");
    expect(shortcutFor("onReject").chord.display).toBe("⌘⌫");
  });
});

describe("normalizeShortcutKey", () => {
  it("lowercases single-character keys", () => {
    expect(normalizeShortcutKey("M")).toBe("m");
    expect(normalizeShortcutKey("k")).toBe("k");
    expect(normalizeShortcutKey(",")).toBe(",");
  });

  it("leaves named (multi-character) keys unchanged", () => {
    expect(normalizeShortcutKey("ArrowLeft")).toBe("ArrowLeft");
    expect(normalizeShortcutKey("Enter")).toBe("Enter");
    expect(normalizeShortcutKey("Backspace")).toBe("Backspace");
  });
});

describe("matchesChord — exact-modifier matching (FR-6.12)", () => {
  it("matches every chord's own synthetic event", () => {
    for (const shortcut of SHELL_SHORTCUTS) {
      expect(matchesChord(eventFor(shortcut.chord), shortcut.chord)).toBe(true);
    }
  });

  it("accepts ctrlKey as the command modifier (Win/Linux)", () => {
    const chord = shortcutFor("onNewRun").chord;
    const event = evt({ key: "n", metaKey: false, ctrlKey: true });
    expect(matchesChord(event, chord)).toBe(true);
  });

  it("rejects a missing command modifier", () => {
    const chord = shortcutFor("onNewRun").chord;
    expect(matchesChord(evt({ key: "n", metaKey: false }), chord)).toBe(false);
  });

  it("rejects an extra Alt modifier", () => {
    const chord = shortcutFor("onNewRun").chord;
    expect(matchesChord(evt({ key: "n", altKey: true }), chord)).toBe(false);
  });

  it("rejects an extra Shift on a non-shift chord", () => {
    const chord = shortcutFor("onNewRun").chord;
    expect(matchesChord(evt({ key: "n", shiftKey: true }), chord)).toBe(false);
  });

  it("rejects a missing Shift on a shift chord", () => {
    const chord = shortcutFor("onOpenLocalModelPicker").chord;
    expect(matchesChord(evt({ key: "m" }), chord)).toBe(false);
  });

  it("treats an uppercase letter key the same as its lowercase canonical", () => {
    const chord = shortcutFor("onOpenLocalModelPicker").chord;
    const event = evt({ key: "M", shiftKey: true });
    expect(matchesChord(event, chord)).toBe(true);
  });

  it("keeps ⌘⇧M and ⌘M distinct (no cross-fire)", () => {
    const localModel = shortcutFor("onOpenLocalModelPicker").chord; // ⌘⇧M
    const switchMode = shortcutFor("onSwitchMode").chord; // ⌘M

    const shiftM = evt({ key: "m", shiftKey: true });
    const plainM = evt({ key: "m" });

    expect(matchesChord(shiftM, localModel)).toBe(true);
    expect(matchesChord(shiftM, switchMode)).toBe(false);
    expect(matchesChord(plainM, switchMode)).toBe(true);
    expect(matchesChord(plainM, localModel)).toBe(false);
  });
});
