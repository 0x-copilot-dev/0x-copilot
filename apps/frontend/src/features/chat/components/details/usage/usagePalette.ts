/**
 * PR 4.5 — Stable colour assignment for the workspace usage chart.
 *
 * Returns a deterministic mapping from `user_id` (or any stable string key) to
 * a CSS colour drawn from the design-system accent palette. The "other" bucket
 * always resolves to a neutral colour so it never competes with the named
 * series for visual weight.
 *
 * The mapping is stable across renders for the same input set. Order of the
 * caller-supplied `keys` array determines colour assignment — pass keys in
 * descending-cost order so the largest stack always wears `--color-accent`.
 */

import { ACCENT_SCHEMES } from "@0x-copilot/design-system";

const RAMP: ReadonlyArray<string> = ACCENT_SCHEMES.map((entry) => entry.swatch);
const OTHER_KEY = "__other__" as const;
const OTHER_COLOR = "var(--color-text-subtle, #7e7e84)";

export type UsagePalette = Readonly<Record<string, string>>;

export interface UsagePaletteInput {
  /** Stable keys in priority order (highest weight first). */
  readonly keys: ReadonlyArray<string>;
  /** Set true when the chart includes an "Other" fold-in stack. */
  readonly includeOther?: boolean;
}

export function usagePalette(input: UsagePaletteInput): UsagePalette {
  const result: Record<string, string> = {};
  for (let index = 0; index < input.keys.length; index += 1) {
    const key = input.keys[index];
    result[key] = RAMP[index % RAMP.length];
  }
  if (input.includeOther) {
    result[OTHER_KEY] = OTHER_COLOR;
  }
  return result;
}

export const USAGE_PALETTE_OTHER_KEY = OTHER_KEY;
