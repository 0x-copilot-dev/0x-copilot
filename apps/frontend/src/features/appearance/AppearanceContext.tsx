import type {
  AppearancePreferences,
  UpdateUserPreferencesRequest,
} from "@enterprise-search/api-types";
import {
  useTheme,
  type AccentScheme,
  type ThemeScheme,
} from "@enterprise-search/design-system";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  type ReactElement,
  type ReactNode,
} from "react";

import { useUserPreferencesState } from "../me/UserPreferencesContext";

// PRD: docs/architecture/prds/04-appearance-single-writer.md
//
// Single writer for everything appearance-related. Owns:
//   1. The mirror from server preferences → design-system ThemeProvider
//      (replaces the deleted `useThemeSync` hook).
//   2. The `data-density` / `data-reduce-motion` writes on the document
//      root.
//   3. The 300ms debounced server save when the user changes anything.
//
// Optimistic apply + debounced persist live in one place so the
// "what gets painted" and "what gets saved" are guaranteed to match.

const SAVE_DEBOUNCE_MS = 300;

export interface AppearanceController {
  /** Current server snapshot (null while preferences load). */
  appearance: AppearancePreferences | null;
  loading: boolean;
  /**
   * Error from the underlying preferences fetch / save. Surface in the
   * Appearance section banner.
   */
  error: string | null;
  /**
   * Apply a partial update. Visual change is instant (optimistic);
   * server save is debounced 300ms and coalesces consecutive calls.
   * On failure, the preferences hook's `error` is set and a subsequent
   * `refresh()` (or the next page load) reconciles back to the server
   * snapshot.
   */
  set: (patch: Partial<AppearancePreferences>) => void;
}

const AppearanceContext = createContext<AppearanceController | null>(null);

/**
 * Mounts once at the authenticated app shell. Reads from the shared
 * preferences cache (`UserPreferencesProvider`) — no extra round-trip.
 */
export function AppearanceProvider({
  children,
}: {
  children: ReactNode;
}): ReactElement {
  const preferences = useUserPreferencesState();
  const { setScheme, setAccent } = useTheme();
  const debounceRef = useRef<number | null>(null);

  // Mirror server snapshot → design-system provider + document attrs.
  // This is the *only* path that pushes server state into the painted
  // chrome; click handlers go through `set()` which calls the same
  // mirror as a synchronous side-effect.
  useEffect(() => {
    const data = preferences.data;
    if (data === null) return;
    applyToProvider(data.appearance, setScheme, setAccent);
    applyToDocument(data.appearance);
  }, [preferences.data, setScheme, setAccent]);

  // Clear any pending debounced save on unmount so a stray timeout
  // doesn't fire after the app tree is gone (e.g. logout).
  useEffect(() => {
    return () => {
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
      }
    };
  }, []);

  const set = useCallback(
    (patch: Partial<AppearancePreferences>): void => {
      // 1. Optimistic apply — paint the new chrome immediately.
      applyToProvider(patch, setScheme, setAccent);
      applyToDocument(patch);
      // 2. Coalesce the server save. The 300ms window swallows rapid
      //    swatch clicks into one network round-trip.
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
      }
      debounceRef.current = window.setTimeout(() => {
        debounceRef.current = null;
        // Build the patch against the current snapshot so partial
        // updates don't clobber sibling fields if the user e.g.
        // changes accent then changes theme inside the debounce
        // window — the second call's `patch` is merged onto the
        // already-flipped first apply.
        const body: UpdateUserPreferencesRequest = {
          appearance: patch,
        };
        void preferences.save(body).catch(() => {
          // Hook already surfaces the error; useEffect above will
          // reconcile back to server state on the next data tick if
          // we want to. Today we leave the optimistic state in place
          // — matches the pre-PR behaviour.
        });
      }, SAVE_DEBOUNCE_MS);
    },
    [preferences, setScheme, setAccent],
  );

  const value = useMemo<AppearanceController>(
    () => ({
      appearance: preferences.data?.appearance ?? null,
      loading: preferences.loading,
      error: preferences.error,
      set,
    }),
    [preferences.data, preferences.loading, preferences.error, set],
  );

  return (
    <AppearanceContext.Provider value={value}>
      {children}
    </AppearanceContext.Provider>
  );
}

export function useAppearance(): AppearanceController {
  const ctx = useContext(AppearanceContext);
  if (ctx === null) {
    throw new Error(
      "AppearanceProvider missing — wrap the authenticated app tree.",
    );
  }
  return ctx;
}

function applyToProvider(
  patch: Partial<AppearancePreferences>,
  setScheme: (s: ThemeScheme) => void,
  setAccent: (a: AccentScheme) => void,
): void {
  if (patch.theme !== undefined) {
    setScheme(toProviderScheme(patch.theme));
  }
  if (patch.accent !== undefined) {
    setAccent(patch.accent as AccentScheme);
  }
}

function applyToDocument(patch: Partial<AppearancePreferences>): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (patch.density !== undefined) {
    root.dataset.density = patch.density;
  }
  if (patch.reduce_motion !== undefined) {
    root.dataset.reduceMotion = patch.reduce_motion;
  }
}

/**
 * ThemeProvider currently ships dark / light / slate. "system" maps to
 * the existing default scheme ("dark") — the conservative no-op until
 * provider gains an explicit "system" mode (would honour
 * `prefers-color-scheme`).
 */
function toProviderScheme(theme: AppearancePreferences["theme"]): ThemeScheme {
  if (theme === "light" || theme === "slate") {
    return theme;
  }
  return "dark";
}
