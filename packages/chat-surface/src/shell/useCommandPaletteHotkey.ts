// useCommandPaletteHotkey — global ⌘K / Ctrl+K listener that calls
// `onOpen()` when the user wants to open the palette.
//
// Source: team-memory-cmdk-prd.md §7.3 (hotkey contract) — ⌘K (Mac) /
// Ctrl+K (Win/Linux) opens; Esc closes is owned by the palette itself.
//
// The listener is mounted on `globalThis.document` (same substrate
// convention as the HashRouter, so SSR / non-DOM hosts no-op). We
// detach on unmount and on `onOpen` identity change so a re-render
// with a fresh callback never leaks the previous listener.

import { useEffect } from "react";

export interface UseCommandPaletteHotkeyOptions {
  /** Called when the user presses ⌘K (Mac) / Ctrl+K (Win/Linux). */
  readonly onOpen: () => void;
  /** When false, the listener is detached. Defaults to true. */
  readonly enabled?: boolean;
}

export function useCommandPaletteHotkey({
  onOpen,
  enabled = true,
}: UseCommandPaletteHotkeyOptions): void {
  useEffect(() => {
    if (!enabled) {
      return;
    }
    const doc = globalThis.document;
    if (doc === undefined) {
      return;
    }
    const onKeyDown = (event: KeyboardEvent): void => {
      const isPaletteToggle =
        (event.metaKey || event.ctrlKey) &&
        !event.shiftKey &&
        !event.altKey &&
        event.key.toLowerCase() === "k";
      if (isPaletteToggle) {
        event.preventDefault();
        onOpen();
      }
    };
    doc.addEventListener("keydown", onKeyDown);
    return () => {
      doc.removeEventListener("keydown", onKeyDown);
    };
  }, [onOpen, enabled]);
}
