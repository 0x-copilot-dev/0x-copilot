// useRailWidth — KeyValueStore-backed width of the Studio workspace rail.
//
// The rail (chat/tabs column) width is a resizable, persisted layout preference.
// Unlike Studio/Focus mode (per-conversation, see useRunMode), the rail width is
// GLOBAL — one width the user sets once, applied to every run. Persistence goes
// through the same KeyValueStore port useRunMode uses (web → localStorage,
// desktop → the shell's store), so it is substrate-agnostic.

import { useCallback, useState } from "react";

import { useKeyValueStore } from "../../providers/KeyValueStoreProvider";
import { clampRailWidth, DEFAULT_RAIL_WIDTH } from "../../thread-canvas";

/** KeyValueStore key for the persisted Studio rail width. Shares the
 *  `chats.*` app-preference namespace. */
export const RAIL_WIDTH_KEY = "chats.rail_width";

export interface UseRailWidthResult {
  /** Current rail width in px (always within the clamp range). */
  readonly width: number;
  /** Set + persist the rail width (clamped to the allowed range). */
  readonly setWidth: (width: number) => void;
}

/**
 * Read the persisted rail width, defaulting + clamping. A missing or
 * unparseable value resolves to the default, so an older/newer client degrades
 * safely instead of throwing.
 */
export function readRailWidth(store: {
  get(key: string): string | null;
}): number {
  const raw = store.get(RAIL_WIDTH_KEY);
  const parsed = raw === null ? Number.NaN : Number(raw);
  return Number.isFinite(parsed) ? clampRailWidth(parsed) : DEFAULT_RAIL_WIDTH;
}

export function useRailWidth(): UseRailWidthResult {
  const store = useKeyValueStore();
  const [width, setWidthState] = useState<number>(() => readRailWidth(store));

  const setWidth = useCallback(
    (next: number): void => {
      const clamped = clampRailWidth(next);
      store.set(RAIL_WIDTH_KEY, String(clamped));
      setWidthState(clamped);
    },
    [store],
  );

  return { width, setWidth };
}
