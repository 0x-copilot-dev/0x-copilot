// useAppearanceSettings (PRD-12 D9 / README G7) — boot-load + persist controller
// for the app's appearance, over the ports that already ship.
//
// The gap it closes: desktop mounts no design-system `ThemeProvider`
// (`ast-allowlist.ts:18`), so `:root[data-accent]` is the ONLY accent mechanism,
// yet nothing read the saved appearance at boot and nothing wrote it on change —
// every launch snapped back to sky. PRD-01's nine restored accents were
// unobservable on the primary substrate until this lands.
//
// Nothing new is invented; this composes what exists:
//   * the attribute contract `appearanceAttributes` (`AppearancePage.tsx`),
//   * the persistence classifier `splitAppearancePersistence` (routes each field
//     to `Transport` (contract) or `KeyValueStore` (off-contract) — its FIRST
//     host call site),
//   * the shipped `GET`/`PUT /v1/me/preferences` round-trip.
//
// Substrate-agnostic: `document` is an eslint-banned global here, so the host
// paints the attributes via the injected `onApply` (the two-line DOM write it
// already owns). This controller only decides the value and where it persists.

import type {
  AppearancePreferences,
  UserPreferences,
} from "@0x-copilot/api-types";
import type { Transport } from "@0x-copilot/chat-transport";
import { useCallback, useEffect, useRef, useState } from "react";

import type { KeyValueStore } from "../storage/key-value-store";

import {
  appearanceAttributes,
  splitAppearancePersistence,
  type AppearanceAttributes,
  type AppearancePatch,
  type AppearanceValue,
} from "./AppearancePage";

const PREFERENCES_PATH = "/v1/me/preferences";
// The single KeyValueStore key the off-contract half persists under (the fields
// `splitAppearancePersistence` classifies as `local` — today `jade`/`ember`
// accents and `spacious` density).
const LOCAL_KEY = "chat-surface.appearance.local";
// Mirrors web's `AppearanceContext` SAVE_DEBOUNCE_MS so a run of swatch clicks
// coalesces to ONE PUT round-trip.
const SAVE_DEBOUNCE_MS = 300;

const DEFAULT_APPEARANCE: AppearanceValue = {
  theme: "system",
  accent: "sky",
  density: "comfortable",
  reduceMotion: false,
};

export interface AppearanceSettingsPorts {
  readonly transport: Transport;
  readonly keyValueStore: KeyValueStore;
  /** Host paints the attributes; the package must not touch `document`. */
  readonly onApply: (attrs: AppearanceAttributes) => void;
}

export interface AppearanceSettingsController {
  readonly value: AppearanceValue;
  readonly loading: boolean;
  readonly error: string | null;
  readonly change: (patch: AppearancePatch) => void;
}

type LocalOverlay = Partial<Pick<AppearanceValue, "accent" | "density">>;

function readLocalOverlay(store: KeyValueStore): LocalOverlay {
  const raw = store.get(LOCAL_KEY);
  if (raw === null) return {};
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const overlay: { accent?: string; density?: string } = {};
    if (typeof parsed.accent === "string") overlay.accent = parsed.accent;
    if (typeof parsed.density === "string") overlay.density = parsed.density;
    return overlay;
  } catch {
    return {};
  }
}

function valueFromPreferences(
  prefs: UserPreferences,
  overlay: LocalOverlay,
): AppearanceValue {
  const appearance = prefs.appearance;
  return {
    theme: appearance.theme,
    // Off-contract accent/density (if any) win — the server never stored them,
    // so the KV overlay is their only home until PRD-01 widens the contract.
    accent: overlay.accent ?? appearance.accent,
    density: overlay.density ?? appearance.density,
    reduceMotion: appearance.reduce_motion === "always",
  };
}

function errorMessage(err: unknown): string {
  return err instanceof Error && err.message
    ? err.message
    : "Could not load appearance preferences.";
}

export function useAppearanceSettings(
  ports: AppearanceSettingsPorts,
): AppearanceSettingsController {
  const { transport, keyValueStore, onApply } = ports;
  const [value, setValue] = useState<AppearanceValue>(DEFAULT_APPEARANCE);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Refs so the boot effect and `change` read the live ports without re-running
  // (a host may pass a fresh `onApply` identity per render).
  const onApplyRef = useRef(onApply);
  onApplyRef.current = onApply;
  const transportRef = useRef(transport);
  transportRef.current = transport;
  const keyValueStoreRef = useRef(keyValueStore);
  keyValueStoreRef.current = keyValueStore;

  // Boot: load the server snapshot, overlay the off-contract locals, and paint
  // before any user interaction. This is the load-bearing half G7 lacked.
  //
  // StrictMode-safe: the ONLY guard is the per-effect `cancelled` flag (the
  // React-idiomatic fetch-in-effect pattern). A persistent "already booted" ref
  // must NOT be used here — under `<StrictMode>` (the desktop renderer mounts
  // the tree in it) React runs mount → cleanup → mount, so such a ref would let
  // the first effect start the fetch, the cleanup cancel it, and the second
  // effect short-circuit — leaving NOTHING painted. With `cancelled` alone the
  // second effect re-fetches and paints; the first (cancelled) fetch is skipped.
  useEffect(() => {
    let cancelled = false;
    const overlay = readLocalOverlay(keyValueStoreRef.current);
    void transportRef.current
      .request<UserPreferences>({ method: "GET", path: PREFERENCES_PATH })
      .then((prefs) => {
        if (cancelled) return;
        const next = valueFromPreferences(prefs, overlay);
        setValue(next);
        setLoading(false);
        onApplyRef.current(appearanceAttributes(next));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // Offline / first launch: fall back to defaults + the KV overlay and
        // still paint, so the worst case is today's behaviour (the static
        // `index.html` defaults cover the pre-hydration frame), not a blank root.
        const next: AppearanceValue = { ...DEFAULT_APPEARANCE, ...overlay };
        setValue(next);
        setLoading(false);
        setError(errorMessage(err));
        onApplyRef.current(appearanceAttributes(next));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Debounced-PUT machinery. The `local` (KV) half writes synchronously; only
  // the `profile` (contract) half is debounced, coalescing a swatch burst.
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingProfileRef = useRef<Partial<AppearancePreferences>>({});

  useEffect(
    () => () => {
      if (debounceRef.current !== null) clearTimeout(debounceRef.current);
    },
    [],
  );

  const change = useCallback((patch: AppearancePatch) => {
    // Optimistic: apply the user's click immediately and never undo it, even on
    // a failed save (the paint stays; only `error` is set).
    setValue((prev) => {
      const next: AppearanceValue = { ...prev, ...patch };
      onApplyRef.current(appearanceAttributes(next));
      return next;
    });

    const split = splitAppearancePersistence(patch);

    // Off-contract fields → KeyValueStore, the `local` half ONLY. Never a shadow
    // copy of contract fields, so a later contract widening can't read a stale
    // KV value.
    if (Object.keys(split.local).length > 0) {
      const existing = readLocalOverlay(keyValueStoreRef.current);
      keyValueStoreRef.current.set(
        LOCAL_KEY,
        JSON.stringify({ ...existing, ...split.local }),
      );
    }

    // Contract fields → one debounced PUT.
    if (Object.keys(split.profile).length > 0) {
      pendingProfileRef.current = {
        ...pendingProfileRef.current,
        ...split.profile,
      };
      if (debounceRef.current !== null) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        debounceRef.current = null;
        const appearance = pendingProfileRef.current;
        pendingProfileRef.current = {};
        void transportRef.current
          .request({
            method: "PUT",
            path: PREFERENCES_PATH,
            body: { appearance },
          })
          .then(() => {
            // No success is reported (appearance has no SaveBar); a prior error
            // is cleared once a later save lands.
            setError(null);
          })
          .catch((err: unknown) => {
            setError(errorMessage(err));
          });
      }, SAVE_DEBOUNCE_MS);
    }
  }, []);

  return { value, loading, error, change };
}
