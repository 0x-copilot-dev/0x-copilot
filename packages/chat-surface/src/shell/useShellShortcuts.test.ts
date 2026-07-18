// useShellShortcuts — dispatch, exact-modifier match, and input-guard tests.

import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  SHELL_SHORTCUTS,
  type ShortcutChord,
  type ShortcutIntent,
} from "./shortcuts";
import { attachShellShortcuts, useShellShortcuts } from "./useShellShortcuts";

type Handler = ReturnType<typeof vi.fn>;

function chordFor(intent: ShortcutIntent): ShortcutChord {
  const found = SHELL_SHORTCUTS.find((s) => s.intent === intent);
  if (found === undefined) {
    throw new Error(`missing shortcut for intent ${intent}`);
  }
  return found.chord;
}

function keyInit(
  chord: ShortcutChord,
  extra: KeyboardEventInit = {},
): KeyboardEventInit {
  return {
    key: chord.key,
    metaKey: true,
    shiftKey: chord.shift,
    bubbles: true,
    ...extra,
  };
}

function dispatchOn(target: EventTarget, init: KeyboardEventInit): void {
  target.dispatchEvent(
    new KeyboardEvent("keydown", { bubbles: true, ...init }),
  );
}

function dispatchOnDocument(init: KeyboardEventInit): void {
  dispatchOn(document, init);
}

/** Create, append, and focus an editable element; caller must remove it. */
function mountEditable(
  tag: "textarea" | "input",
): HTMLTextAreaElement | HTMLInputElement {
  const el = document.createElement(tag);
  document.body.appendChild(el);
  el.focus();
  return el;
}

describe("useShellShortcuts — dispatch", () => {
  it("fires the matching callback for every §6 chord", () => {
    const handlers = Object.fromEntries(
      SHELL_SHORTCUTS.map((s) => [s.intent, vi.fn()]),
    ) as Record<ShortcutIntent, Handler>;

    renderHook(() => useShellShortcuts(handlers));

    for (const shortcut of SHELL_SHORTCUTS) {
      dispatchOnDocument(keyInit(shortcut.chord));
      expect(handlers[shortcut.intent]).toHaveBeenCalledTimes(1);
    }
  });

  it("makes a chord a no-op when its callback is undefined (FR-6.10)", () => {
    const onOpenPalette = vi.fn();
    renderHook(() => useShellShortcuts({ onOpenPalette }));

    // ⌘N has no handler supplied — must not throw and must not fire anything.
    expect(() =>
      dispatchOnDocument(keyInit(chordFor("onNewRun"))),
    ).not.toThrow();
    expect(onOpenPalette).not.toHaveBeenCalled();
  });

  it("keeps ⌘⇧M and ⌘M distinct (no cross-fire) (FR-6.12)", () => {
    const onOpenLocalModelPicker = vi.fn();
    const onSwitchMode = vi.fn();
    renderHook(() =>
      useShellShortcuts({ onOpenLocalModelPicker, onSwitchMode }),
    );

    dispatchOnDocument(keyInit(chordFor("onOpenLocalModelPicker"))); // ⌘⇧M
    expect(onOpenLocalModelPicker).toHaveBeenCalledTimes(1);
    expect(onSwitchMode).not.toHaveBeenCalled();

    dispatchOnDocument(keyInit(chordFor("onSwitchMode"))); // ⌘M
    expect(onSwitchMode).toHaveBeenCalledTimes(1);
    expect(onOpenLocalModelPicker).toHaveBeenCalledTimes(1);
  });
});

describe("useShellShortcuts — exact-modifier matching (FR-6.12)", () => {
  it("rejects a missing command modifier", () => {
    const onNewRun = vi.fn();
    renderHook(() => useShellShortcuts({ onNewRun }));
    dispatchOnDocument({ key: "n", bubbles: true });
    expect(onNewRun).not.toHaveBeenCalled();
  });

  it("rejects an extra Alt modifier", () => {
    const onNewRun = vi.fn();
    renderHook(() => useShellShortcuts({ onNewRun }));
    dispatchOnDocument({ key: "n", metaKey: true, altKey: true });
    expect(onNewRun).not.toHaveBeenCalled();
  });

  it("rejects an extra Shift on a non-shift chord (⌘⇧N is not ⌘N)", () => {
    const onNewRun = vi.fn();
    renderHook(() => useShellShortcuts({ onNewRun }));
    dispatchOnDocument({ key: "n", metaKey: true, shiftKey: true });
    expect(onNewRun).not.toHaveBeenCalled();
  });
});

describe("useShellShortcuts — input guard (FR-6.11)", () => {
  it("suppresses nav/run chords while a <textarea> is focused, but allows ⌘K and ⌘,", () => {
    const onNewRun = vi.fn(); // ⌘N — global, not input-safe
    const onSwitchMode = vi.fn(); // ⌘M — run-scoped, not input-safe
    const onOpenPalette = vi.fn(); // ⌘K — input-safe
    const onOpenSettings = vi.fn(); // ⌘, — input-safe

    renderHook(() =>
      useShellShortcuts({
        onNewRun,
        onSwitchMode,
        onOpenPalette,
        onOpenSettings,
      }),
    );

    const textarea = mountEditable("textarea");

    dispatchOn(textarea, keyInit(chordFor("onNewRun")));
    dispatchOn(textarea, keyInit(chordFor("onSwitchMode")));
    expect(onNewRun).not.toHaveBeenCalled();
    expect(onSwitchMode).not.toHaveBeenCalled();

    dispatchOn(textarea, keyInit(chordFor("onOpenPalette")));
    dispatchOn(textarea, keyInit(chordFor("onOpenSettings")));
    expect(onOpenPalette).toHaveBeenCalledTimes(1);
    expect(onOpenSettings).toHaveBeenCalledTimes(1);

    textarea.remove();
  });

  it("suppresses non-input-safe chords while an <input> is focused", () => {
    const onNewRun = vi.fn();
    const onOpenPalette = vi.fn();
    renderHook(() => useShellShortcuts({ onNewRun, onOpenPalette }));

    const input = mountEditable("input");

    dispatchOn(input, keyInit(chordFor("onNewRun")));
    expect(onNewRun).not.toHaveBeenCalled();

    dispatchOn(input, keyInit(chordFor("onOpenPalette")));
    expect(onOpenPalette).toHaveBeenCalledTimes(1);

    input.remove();
  });

  it("suppresses non-input-safe chords while a contenteditable is focused", () => {
    const onNewRun = vi.fn();
    renderHook(() => useShellShortcuts({ onNewRun }));

    const editable = document.createElement("div");
    editable.contentEditable = "true";
    document.body.appendChild(editable);

    dispatchOn(editable, keyInit(chordFor("onNewRun")));
    expect(onNewRun).not.toHaveBeenCalled();

    editable.remove();
  });
});

describe("useShellShortcuts — lifecycle", () => {
  it("detaches the listener on unmount", () => {
    const onNewRun = vi.fn();
    const { unmount } = renderHook(() => useShellShortcuts({ onNewRun }));
    unmount();
    dispatchOnDocument(keyInit(chordFor("onNewRun")));
    expect(onNewRun).not.toHaveBeenCalled();
  });

  it("does not attach the listener when enabled is false", () => {
    const onNewRun = vi.fn();
    renderHook(() => useShellShortcuts({ onNewRun, enabled: false }));
    dispatchOnDocument(keyInit(chordFor("onNewRun")));
    expect(onNewRun).not.toHaveBeenCalled();
  });
});

describe("attachShellShortcuts — substrate guard (FR-6.9)", () => {
  it("no-ops (no throw) when document is undefined", () => {
    const onNewRun = vi.fn();
    let detach!: () => void;
    expect(() => {
      detach = attachShellShortcuts(undefined, { onNewRun });
    }).not.toThrow();
    expect(() => detach()).not.toThrow();
    expect(onNewRun).not.toHaveBeenCalled();
  });

  it("attaches to a real document and detaches cleanly", () => {
    const onNewRun = vi.fn();
    const detach = attachShellShortcuts(document, { onNewRun });

    dispatchOnDocument(keyInit(chordFor("onNewRun")));
    expect(onNewRun).toHaveBeenCalledTimes(1);

    detach();
    dispatchOnDocument(keyInit(chordFor("onNewRun")));
    expect(onNewRun).toHaveBeenCalledTimes(1);
  });
});
