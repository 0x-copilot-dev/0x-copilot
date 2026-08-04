// useThreadSwitcherOpen — the Threads panel's open state, persisted PER WIDTH
// CLASS.
//
// Source: docs/plan/windowed-mode/PRD-01-thread-switching.md (D-1.4, FR-1.4,
// FR-1.5).
//
// Why per width class and not one global flag: a single boolean means opening
// the app in a narrow window and later maximising leaves the panel shut on a
// screen with room for it — and vice versa, a docked panel you closed at 1400px
// would come back as an overlay at 640px. The two are different decisions.
//
// `compact` is deliberately NOT persisted and always starts closed: an overlay
// that restores itself open on launch is covering the transcript you came to
// read. It reads state from a ref-free constant so there is no key to migrate.
//
// The value is GLOBAL (one preference across conversations), unlike
// `useRunMode`'s per-conversation mode: "do I want a thread list" is a layout
// habit, not a property of the thread you happen to be in.

import { useCallback, useEffect, useRef, useState } from "react";

import { useKeyValueStore } from "../../providers/KeyValueStoreProvider";
import type { ShellWidthClass } from "../../shell/layout";

/** KeyValueStore key prefix. Shares the `chats.*` app-preference namespace with
 *  `RAIL_WIDTH_KEY`, so all cockpit layout prefs sit together. */
const THREAD_SWITCHER_KEY_PREFIX = "chats.thread_switcher_open.";

/** Per-width-class KeyValueStore key. */
export function threadSwitcherOpenKey(widthClass: ShellWidthClass): string {
  return `${THREAD_SWITCHER_KEY_PREFIX}${widthClass}`;
}

/**
 * Default open state when nothing is persisted: CLOSED at every width.
 *
 * `wide` used to open itself, on the reasoning that 1120px+ has room for a
 * 224px column. Room is not the same as want: the cockpit's job on arrival is
 * the run you are in, and a thread list you did not ask for is 224px of
 * navigation in front of the surface plus the chat rail — most visibly on the
 * first run, where the list holds nothing worth switching to.
 *
 * The panel stays one toggle away (`ThreadSwitcherToggle`), and the moment
 * someone opens it the choice is persisted per width class, so this default
 * only ever describes a cockpit nobody has expressed a preference about.
 * `compact` is an overlay and never opens itself (FR-1.5).
 */
export function defaultThreadSwitcherOpen(
  _widthClass: ShellWidthClass,
): boolean {
  return false;
}

export interface UseThreadSwitcherOpenResult {
  readonly open: boolean;
  readonly setOpen: (next: boolean) => void;
  readonly toggle: () => void;
}

export function useThreadSwitcherOpen(
  widthClass: ShellWidthClass,
): UseThreadSwitcherOpenResult {
  const store = useKeyValueStore();
  const persisted = widthClass !== "compact";
  const key = threadSwitcherOpenKey(widthClass);

  const read = useCallback((): boolean => {
    if (!persisted) {
      // FR-1.5 — the overlay is never restored open.
      return false;
    }
    const raw = store.get(key);
    if (raw === "1") return true;
    if (raw === "0") return false;
    return defaultThreadSwitcherOpen(widthClass);
  }, [key, persisted, store, widthClass]);

  const [open, setOpenState] = useState<boolean>(read);

  // Re-read when the width class changes: crossing a breakpoint must adopt the
  // state persisted for the class you landed in, not carry the old one over.
  useEffect(() => {
    setOpenState(read());
  }, [read]);

  const setOpen = useCallback(
    (next: boolean): void => {
      if (persisted) {
        store.set(key, next ? "1" : "0");
      }
      setOpenState(next);
    },
    [key, persisted, store],
  );

  const openRef = useRef(open);
  useEffect(() => {
    openRef.current = open;
  }, [open]);

  const toggle = useCallback((): void => {
    setOpen(!openRef.current);
  }, [setOpen]);

  return { open, setOpen, toggle };
}
