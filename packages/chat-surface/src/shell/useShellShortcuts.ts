// useShellShortcuts — framework-agnostic keydown listener that fires the
// DESIGN-SPEC.md §6 chord set (defined once in `shortcuts.ts`) against
// caller-supplied callbacks.
//
// Substrate rules (same convention as `useCommandPaletteHotkey` / HashRouter):
// the listener attaches to `globalThis.document` and the hook no-ops when
// `globalThis.document === undefined`, so SSR / non-DOM hosts never throw.
//
// Behaviour:
//   • exact-modifier matching (`matchesChord`) so `⌘⇧M` never fires `⌘M`
//     and vice-versa (FR-6.12);
//   • an input guard that suppresses non-input-safe chords while a text
//     input/textarea/select/contenteditable is focused, while still letting
//     `⌘K` / `⌘,` through (FR-6.11);
//   • an undefined callback makes its chord a no-op (FR-6.10).
//
// The hook fires every chord regardless of `scope`; guarding run-scoped chords
// to the active destination is the desktop wiring's job (PR-6.6, FR-6.13).
//
// Pass a stable / memoized options object: the listener re-attaches whenever
// `options` changes identity.

import { useEffect } from "react";

import {
  SHELL_SHORTCUTS,
  matchesChord,
  type UseShellShortcutsOptions,
} from "./shortcuts";

/** True when the keyboard event would land on an editable control (FR-6.11). */
function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
    return true;
  }
  // `isContentEditable` is the right answer in real browsers; jsdom returns
  // `undefined` for un-attached elements, so we also accept the attribute.
  return target.isContentEditable === true || target.contentEditable === "true";
}

/**
 * Attach the §6 shortcut listener to `doc` and return a detach function.
 * No-ops (returning a harmless detach) when `doc === undefined`, which is the
 * guarded seam the hook relies on for non-DOM hosts. Exported so the
 * `document === undefined` branch is directly testable without a DOM.
 */
export function attachShellShortcuts(
  doc: Document | undefined,
  options: UseShellShortcutsOptions,
): () => void {
  if (doc === undefined) {
    return () => {};
  }
  const onKeyDown = (event: KeyboardEvent): void => {
    const editable = isEditableTarget(event.target);
    for (const shortcut of SHELL_SHORTCUTS) {
      if (!matchesChord(event, shortcut.chord)) {
        continue;
      }
      // At most one chord matches a given event (the table's key/shift pairs
      // are unique), so we always return after the first match.
      if (editable && !shortcut.inputSafe) {
        // Typing: let the keystroke reach the field. No preventDefault.
        return;
      }
      const handler = options[shortcut.intent];
      if (handler !== undefined) {
        event.preventDefault();
        handler();
      }
      return;
    }
  };
  doc.addEventListener("keydown", onKeyDown);
  return () => {
    doc.removeEventListener("keydown", onKeyDown);
  };
}

/**
 * Register the DESIGN-SPEC.md §6 keyboard shortcuts for the lifetime of the
 * calling component. See the module header for behaviour and the substrate
 * contract.
 */
export function useShellShortcuts(options: UseShellShortcutsOptions): void {
  const { enabled = true } = options;
  useEffect(() => {
    if (!enabled) {
      return;
    }
    return attachShellShortcuts(globalThis.document, options);
  }, [options, enabled]);
}
