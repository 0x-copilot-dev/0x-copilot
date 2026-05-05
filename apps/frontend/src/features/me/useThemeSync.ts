import type { UserPreferences } from "@enterprise-search/api-types";
import {
  useTheme,
  type AccentScheme,
  type ThemeScheme,
} from "@enterprise-search/design-system";
import { useEffect } from "react";

/**
 * One-way mirror from server preferences → ``ThemeProvider`` + the
 * ``<html>`` ``data-density`` / ``data-reduce-motion`` attributes.
 *
 * The localStorage cache that ThemeProvider writes on every change
 * stays — it's the paint-flicker avoidance we want at first paint on
 * a fresh device. On a cold visit:
 *
 *   1. ThemeProvider hydrates from localStorage (or default) — no flicker.
 *   2. ``useUserPreferences`` fetches the server row.
 *   3. This effect mirrors the server row into the provider; if the
 *      server says something different, the provider updates and the
 *      cache catches up via its own write effect.
 *
 * Theme = "system" maps to ``ThemeProvider``'s "dark" scheme today —
 * we follow the OS preference via ``prefers-color-scheme`` later if
 * it becomes a v2 ask. Treating "system" as "dark" matches the
 * existing default and keeps the typesystem honest until we add the
 * fourth provider scheme.
 */
export function useThemeSync(preferences: UserPreferences | null): void {
  const { setScheme, setAccent } = useTheme();

  useEffect(() => {
    if (preferences === null) {
      return;
    }
    setScheme(toProviderScheme(preferences.appearance.theme));
    setAccent(preferences.appearance.accent as AccentScheme);
  }, [preferences, setScheme, setAccent]);

  useEffect(() => {
    if (typeof document === "undefined") {
      return;
    }
    const root = document.documentElement;
    if (preferences === null) {
      // Leave defaults in place until the server hydrates so a slow
      // network doesn't flash compact + reduce-motion off then on.
      return;
    }
    root.dataset.density = preferences.appearance.density;
    root.dataset.reduceMotion = preferences.appearance.reduce_motion;
  }, [preferences]);
}

function toProviderScheme(
  theme: UserPreferences["appearance"]["theme"],
): ThemeScheme {
  // ThemeProvider currently ships dark / light / slate. "system" here
  // mirrors the OS via the existing ``color-scheme: dark`` default —
  // converting to "dark" is the conservative no-op until provider
  // gains an explicit "system" scheme.
  if (theme === "light" || theme === "slate") {
    return theme;
  }
  return "dark";
}
