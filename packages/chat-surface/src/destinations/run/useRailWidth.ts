// useRailWidth — KeyValueStore-backed width of the Studio workspace rail.
//
// The rail (chat/tabs column) width is a resizable, persisted layout preference.
// Unlike Studio/Focus mode (per-conversation, see useRunMode), the rail width is
// GLOBAL — one width the user sets once, applied to every run. Persistence goes
// through the same KeyValueStore port useRunMode uses (web → localStorage,
// desktop → the shell's store), so it is substrate-agnostic.

import { useCallback, useState } from "react";

import { useKeyValueStore } from "../../providers/KeyValueStoreProvider";
import { clampRailWidth } from "../../thread-canvas";

/** KeyValueStore key for the persisted Studio rail width. Shares the
 *  `chats.*` app-preference namespace. */
export const RAIL_WIDTH_KEY = "chats.rail_width";

/**
 * Share of the canvas the chat rail takes when nobody has dragged it.
 *
 * A FRACTION, not a pixel count. The default used to be a flat 584px, which is
 * a different split at every window size — 41% of a 1440px cockpit, 49% of a
 * 1200px one — so the generative surface got squeezed hardest exactly on the
 * screens with least room. Expressed as a share, the surface keeps ~68% and the
 * chat ~32% whatever the window does.
 *
 * Still only a DEFAULT: the drag handle writes px to the store (a share would
 * make a deliberate width drift as the window resizes), and a stored value
 * always wins.
 */
export const COCKPIT_RAIL_WIDTH_FRACTION = 0.32;

/**
 * The unpersisted rail width for a canvas this wide, clamped to the allowed
 * range — so a very narrow cockpit still gets a usable composer and a very wide
 * one does not hand half the screen to chat.
 */
export function cockpitDefaultRailWidth(canvasWidthPx: number): number {
  return clampRailWidth(canvasWidthPx * COCKPIT_RAIL_WIDTH_FRACTION);
}

export interface UseRailWidthResult {
  /**
   * The user's persisted width in px, or `null` when they have never dragged
   * the handle. `null` is not "no width" — it is "no preference", and the
   * consumer resolves it against the live canvas width via
   * {@link cockpitDefaultRailWidth}. The hook cannot do that itself: it runs
   * before the cockpit's ResizeObserver has measured anything.
   */
  readonly width: number | null;
  /** Set + persist the rail width (clamped to the allowed range). */
  readonly setWidth: (width: number) => void;
}

/**
 * Read the persisted rail width. A missing or unparseable value resolves to
 * `null` (no preference), so an older/newer client degrades to the
 * canvas-relative default instead of throwing.
 */
export function readRailWidth(store: {
  get(key: string): string | null;
}): number | null {
  const raw = store.get(RAIL_WIDTH_KEY);
  const parsed = raw === null ? Number.NaN : Number(raw);
  return Number.isFinite(parsed) ? clampRailWidth(parsed) : null;
}

export function useRailWidth(): UseRailWidthResult {
  const store = useKeyValueStore();
  const [width, setWidthState] = useState<number | null>(() =>
    readRailWidth(store),
  );

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
