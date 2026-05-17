// PR 4.4.7 Phase 2 (Slice A) — backend-backed catalog discoverable
// override.
//
// Phase 1 stored this in ``localStorage`` per device. Slice A migrates
// the hook to read/write the same logical preference via the user's
// ``UserPreferences.discoverable_connectors.overrides`` map so the
// toggle survives across browsers and is available to the runtime in
// Slice B.
//
// Migration path on first call within a session:
//   1. Fetch the user's preferences once (shared across all hook
//      instances via a module-level promise).
//   2. If ``localStorage`` has overrides not present in the backend,
//      PATCH them and clear the local entries.
//   3. Subsequent reads come from the in-memory cache + the backend
//      response; writes go through ``updateMyPreferences`` and update
//      the cache optimistically.
//
// The hook keeps Phase 1's surface verbatim — ``(enabled, overridden,
// setEnabled)`` — so the catalog cards do not need to change.
//
// We deliberately do NOT switch to localStorage when the backend call
// fails: a fresh user may have no row yet, in which case the GET
// returns ``overrides: {}`` and the hook reports the catalog default.
// Network failures degrade to "no override" (catalog default) and the
// next setEnabled retries the PATCH; that is the same end-state as
// localStorage was, just without the cross-device persistence.

import {
  useKeyValueStore,
  type KeyValueStore,
} from "@enterprise-search/chat-surface";
import { useCallback, useEffect, useState } from "react";

import { getMyPreferences, updateMyPreferences } from "../../api/meApi";

const LEGACY_PREFIX = "enterprise.discoverable.";

type Overrides = Record<string, boolean>;

interface SharedState {
  overrides: Overrides;
  /** Listeners notified on every write so multiple cards stay in sync. */
  listeners: Set<(next: Overrides) => void>;
}

const SHARED: SharedState = {
  overrides: {},
  listeners: new Set(),
};

let bootstrapPromise: Promise<void> | null = null;
// The store the active bootstrap was initialized with. Captured here so
// the retry-after-persist-failure path can re-run bootstrap with the
// same substrate-bound store without re-threading through the call
// chain. There's only one store per app in practice.
let activeStore: KeyValueStore | null = null;

function readLegacyLocalOverrides(store: KeyValueStore): Overrides {
  const out: Overrides = {};
  try {
    for (const key of store.keys(LEGACY_PREFIX)) {
      const slug = key.slice(LEGACY_PREFIX.length);
      const raw = store.get(key);
      if (raw === "on") out[slug] = true;
      else if (raw === "off") out[slug] = false;
    }
  } catch {
    // Privacy mode / quota errors — return what we have.
  }
  return out;
}

function clearLegacyLocalOverrides(
  store: KeyValueStore,
  slugs: readonly string[],
): void {
  try {
    for (const slug of slugs) {
      store.set(LEGACY_PREFIX + slug, null);
    }
  } catch {
    /* see read */
  }
}

// Defensive read for backend payloads that may pre-date the
// ``discoverable_connectors`` field. An older backend deploy returns
// the prefs blob without this key; treating that as empty keeps the
// hook honest about server state without throwing on
// ``undefined.overrides``.
function readOverrides(prefs: {
  discoverable_connectors?: { overrides?: Record<string, boolean> };
}): Overrides {
  return { ...(prefs.discoverable_connectors?.overrides ?? {}) };
}

async function bootstrap(store: KeyValueStore): Promise<void> {
  try {
    const prefs = await getMyPreferences();
    SHARED.overrides = readOverrides(prefs);

    // One-time migration: any legacy localStorage entries not present
    // in the backend are PATCHed across, then deleted. The merge is
    // intentionally backend-wins so a deliberate later flip on a
    // different device is not overwritten by stale local state.
    const legacy = readLegacyLocalOverrides(store);
    const toMigrate: Overrides = {};
    for (const [slug, value] of Object.entries(legacy)) {
      if (!(slug in SHARED.overrides)) {
        toMigrate[slug] = value;
      }
    }
    if (Object.keys(toMigrate).length > 0) {
      const updated = await updateMyPreferences({
        discoverable_connectors: { overrides: toMigrate },
      });
      SHARED.overrides = readOverrides(updated);
    }
    clearLegacyLocalOverrides(store, Object.keys(legacy));
  } catch {
    // Bootstrap failure (older backend, network blip, etc.) leaves
    // SHARED.overrides empty — the hook will report catalog defaults
    // until the next setEnabled retries. No fallback to localStorage
    // by design (avoids two sources of truth diverging silently).
    SHARED.overrides = {};
  }
}

function ensureBootstrapped(store: KeyValueStore): Promise<void> {
  if (bootstrapPromise === null) {
    activeStore = store;
    bootstrapPromise = bootstrap(store);
  }
  return bootstrapPromise;
}

function publish(next: Overrides): void {
  SHARED.overrides = next;
  for (const listener of SHARED.listeners) {
    listener(next);
  }
}

async function persist(slug: string, enabled: boolean): Promise<void> {
  // Optimistic local update.
  const optimistic = { ...SHARED.overrides, [slug]: enabled };
  publish(optimistic);
  try {
    const updated = await updateMyPreferences({
      discoverable_connectors: { overrides: { [slug]: enabled } },
    });
    // Each PATCH is authoritative ONLY for the slug it sent. Trusting
    // the response's full ``overrides`` map would clobber other slugs'
    // optimistic state when two toggles fire in quick succession: the
    // first PATCH's response is built before the server has seen the
    // second PATCH, so it omits the second slug. The visible symptom
    // is "second toggle flips back on briefly". Merge the response's
    // value for our slug onto the live map and leave everything else
    // alone.
    const echoed = updated.discoverable_connectors?.overrides?.[slug];
    publish({
      ...SHARED.overrides,
      [slug]: typeof echoed === "boolean" ? echoed : enabled,
    });
  } catch {
    // Network failure — revert by re-fetching via bootstrap. Cheap and
    // keeps the UI honest about server state. Re-uses the store the
    // active bootstrap was initialized with; if bootstrap never ran
    // (no hook has mounted yet) we skip the retry.
    bootstrapPromise = null;
    if (activeStore !== null) {
      void ensureBootstrapped(activeStore);
    }
  }
}

export interface DiscoverablePref {
  /** Effective state — user override (if any), else the catalog default. */
  enabled: boolean;
  /** Has the user explicitly set this slug? Drives "Default · …" hint copy. */
  overridden: boolean;
  setEnabled: (next: boolean) => void;
}

/**
 * Reads the user's override for one catalog entry's discoverable flag,
 * falling back to the entry's catalog default when no override exists.
 *
 * Slice A wires the same surface to the user's backend preferences
 * blob so toggles survive across browsers. The hook bootstraps once
 * per session via ``getMyPreferences`` and migrates any leftover
 * Phase 1 ``localStorage`` entries on the way.
 */
export function useDiscoverablePref(
  slug: string,
  catalogDefault: boolean,
): DiscoverablePref {
  const kvStore = useKeyValueStore();
  const [override, setOverride] = useState<boolean | undefined>(() =>
    slug in SHARED.overrides ? SHARED.overrides[slug] : undefined,
  );

  useEffect(() => {
    let cancelled = false;
    const onChange = (next: Overrides): void => {
      if (cancelled) return;
      setOverride(slug in next ? next[slug] : undefined);
    };
    SHARED.listeners.add(onChange);
    void ensureBootstrapped(kvStore).then(() => {
      if (!cancelled) onChange(SHARED.overrides);
    });
    return () => {
      cancelled = true;
      SHARED.listeners.delete(onChange);
    };
  }, [slug, kvStore]);

  const setEnabled = useCallback(
    (next: boolean) => {
      void persist(slug, next);
    },
    [slug],
  );

  return {
    enabled: override ?? catalogDefault,
    overridden: override !== undefined,
    setEnabled,
  };
}

// PR 4.4.7 Phase 2 (Slice A) — testing seam. Lets unit tests reset
// the module-level cache + bootstrap promise so they don't bleed
// state across cases. Production code never calls this.
export function _resetDiscoverablePrefForTests(): void {
  SHARED.overrides = {};
  SHARED.listeners.clear();
  bootstrapPromise = null;
  activeStore = null;
}
