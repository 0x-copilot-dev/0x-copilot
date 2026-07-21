/**
 * Surface-adapter registry: scheme → versioned {@link SaaSRendererAdapter}.
 *
 * ## Scoping invariant (PRD-11, registry scoping groundwork)
 *
 * The registry is available in two forms that share ONE code path:
 *
 * - A process-wide **module-global** instance ({@link globalSurfaceRegistry}),
 *   exposed through the free functions ({@link registerAdapter},
 *   {@link resolveAdapter}, …). This is the historical, default surface and its
 *   behaviour is unchanged: desktop is single-instance, so a global is correct.
 * - An **isolated** instance from {@link createSurfaceRegistry}, whose state
 *   (schemes, versions, broken flags, legacy components) is fully private and
 *   never touches the global. A {@link SurfaceRegistryProvider} publishes one
 *   through React context so a subtree can resolve against it.
 *
 * The invariant the rest of the surface relies on: **absent a provider, every
 * consumer resolves against `globalSurfaceRegistry`** — the same instance the
 * free functions mutate. `createSurfaceRegistry()` unlocks per-tenant scoping
 * for multi-tenant web later without a rewrite, but the default path is
 * byte-for-byte the old global behaviour (zero behaviour change). The free
 * functions are thin delegates to `globalSurfaceRegistry`, so mutating via a
 * free function and reading via the context default observe the same state.
 */
import type { ComponentType, ReactElement } from "react";

import { TIER3_SCHEME, type SaaSRendererAdapter } from "./SaaSRendererAdapter";
import type { SurfaceRendererProps } from "./types";

interface RegistryEntry {
  adapter: SaaSRendererAdapter;
  broken: boolean;
}

/**
 * The stateful surface-registry contract. Both the module-global instance and
 * every isolated instance from {@link createSurfaceRegistry} implement it, so
 * the two are substitutable everywhere (context default, provider value, tests).
 */
export interface SurfaceRegistry {
  registerAdapter(adapter: SaaSRendererAdapter): void;
  resolveAdapter(uri: string): SaaSRendererAdapter | null;
  unregisterAdapter(scheme: string, version?: number): void;
  markBroken(scheme: string, version: number, reason: string): void;
  clearRegistry(): void;
  /** @deprecated Legacy component registration — see {@link registerSurface}. */
  registerSurface(
    scheme: string,
    component: ComponentType<SurfaceRendererProps>,
  ): void;
  /** @deprecated Legacy component lookup — see {@link resolveSurface}. */
  resolveSurface(uri: string): ComponentType<SurfaceRendererProps> | null;
}

// --- Pure, state-free helpers (shared by every instance) -------------------

function insertSorted(bucket: RegistryEntry[], entry: RegistryEntry): void {
  const version = entry.adapter.metadata.schemaVersion;
  for (let i = 0; i < bucket.length; i += 1) {
    if (bucket[i].adapter.metadata.schemaVersion < version) {
      bucket.splice(i, 0, entry);
      return;
    }
  }
  bucket.push(entry);
}

function schemeFromUri(uri: string): string | null {
  if (typeof uri !== "string" || uri.length === 0) {
    return null;
  }
  const idx = uri.indexOf("://");
  if (idx <= 0) {
    return null;
  }
  return uri.slice(0, idx);
}

function pickMatching(
  bucket: readonly RegistryEntry[],
  uri: string,
): SaaSRendererAdapter | null {
  for (const entry of bucket) {
    if (entry.broken) continue;
    if (entry.adapter.matches(uri)) return entry.adapter;
  }
  return null;
}

/**
 * Build an isolated surface registry. Its state — exact-scheme buckets, the
 * wildcard (tier-3) bucket, and the deprecated component map — is closed over
 * here and shared with no other instance. Use for per-tenant scoping via
 * {@link SurfaceRegistryProvider}; the process default is
 * {@link globalSurfaceRegistry}.
 */
export function createSurfaceRegistry(): SurfaceRegistry {
  const exactScheme = new Map<string, RegistryEntry[]>();
  const wildcard: RegistryEntry[] = [];
  const deprecatedComponents = new Map<
    string,
    ComponentType<SurfaceRendererProps>
  >();

  function bucketFor(scheme: string): RegistryEntry[] {
    if (scheme === TIER3_SCHEME) {
      return wildcard;
    }
    let bucket = exactScheme.get(scheme);
    if (!bucket) {
      bucket = [];
      exactScheme.set(scheme, bucket);
    }
    return bucket;
  }

  function registerAdapter(adapter: SaaSRendererAdapter): void {
    const bucket = bucketFor(adapter.scheme);
    const version = adapter.metadata.schemaVersion;
    const sameVersion = bucket.find(
      (e) => e.adapter.metadata.schemaVersion === version,
    );
    if (sameVersion) {
      // Replace + clear broken flag so this is also the tier-2 hot-swap path.
      sameVersion.adapter = adapter;
      sameVersion.broken = false;
      return;
    }
    insertSorted(bucket, { adapter, broken: false });
  }

  function resolveAdapter(uri: string): SaaSRendererAdapter | null {
    const scheme = schemeFromUri(uri);
    if (scheme === null) {
      return null;
    }
    const exact = exactScheme.get(scheme);
    if (exact) {
      const hit = pickMatching(exact, uri);
      if (hit) return hit;
    }
    return pickMatching(wildcard, uri);
  }

  function unregisterAdapter(scheme: string, version?: number): void {
    if (scheme === TIER3_SCHEME) {
      if (version === undefined) {
        wildcard.length = 0;
        return;
      }
      const idx = wildcard.findIndex(
        (e) => e.adapter.metadata.schemaVersion === version,
      );
      if (idx >= 0) wildcard.splice(idx, 1);
      return;
    }
    const bucket = exactScheme.get(scheme);
    if (!bucket) return;
    if (version === undefined) {
      exactScheme.delete(scheme);
      deprecatedComponents.delete(scheme);
      return;
    }
    const idx = bucket.findIndex(
      (e) => e.adapter.metadata.schemaVersion === version,
    );
    if (idx >= 0) {
      bucket.splice(idx, 1);
      if (bucket.length === 0) {
        exactScheme.delete(scheme);
        deprecatedComponents.delete(scheme);
      }
    }
  }

  function markBroken(scheme: string, version: number, _reason: string): void {
    const bucket = scheme === TIER3_SCHEME ? wildcard : exactScheme.get(scheme);
    if (!bucket) return;
    for (const entry of bucket) {
      if (entry.adapter.metadata.schemaVersion === version) {
        entry.broken = true;
        return;
      }
    }
  }

  function clearRegistry(): void {
    exactScheme.clear();
    wildcard.length = 0;
    deprecatedComponents.clear();
  }

  function registerSurface(
    scheme: string,
    component: ComponentType<SurfaceRendererProps>,
  ): void {
    const existing = deprecatedComponents.get(scheme);
    if (existing && existing !== component) {
      throw new Error(
        `registerSurface: scheme "${scheme}" already registered to a different component`,
      );
    }
    deprecatedComponents.set(scheme, component);
    // The wrapping adapter exists so resolveAdapter (the new contract) still
    // sees a registration. renderCurrent rejects because the legacy
    // SurfaceRendererProps shape pulls state via Transport — there is no
    // state argument to forward. Legacy callers must use resolveSurface.
    registerAdapter({
      scheme,
      matches: (uri) => schemeFromUri(uri) === scheme,
      renderCurrent: (): ReactElement => {
        throw new Error(
          `registerSurface: scheme "${scheme}" was registered via the deprecated registerSurface API; the new SaaSRendererAdapter.renderCurrent contract cannot be served by a legacy SurfaceRendererProps component. Use resolveSurface for legacy callers; migrate to registerAdapter for new ones.`,
        );
      },
      renderDiff: (): ReactElement => {
        throw new Error(
          `registerSurface: scheme "${scheme}" was registered via the deprecated registerSurface API; renderDiff is not implemented on the legacy wrapper.`,
        );
      },
      metadata: {
        origin: "first-party",
        schemaVersion: 1,
      },
    });
  }

  function resolveSurface(
    uri: string,
  ): ComponentType<SurfaceRendererProps> | null {
    const scheme = schemeFromUri(uri);
    if (scheme === null) return null;
    return deprecatedComponents.get(scheme) ?? null;
  }

  return {
    registerAdapter,
    resolveAdapter,
    unregisterAdapter,
    markBroken,
    clearRegistry,
    registerSurface,
    resolveSurface,
  };
}

/**
 * The process-wide default registry. The free functions below delegate here,
 * and it is the React-context default (see `SurfaceRegistryContext`), so a
 * consumer with no {@link SurfaceRegistryProvider} above it resolves against
 * exactly the state the free functions mutate.
 */
export const globalSurfaceRegistry: SurfaceRegistry = createSurfaceRegistry();

// --- Module-global free functions (delegates; behaviour unchanged) ----------

export function registerAdapter(adapter: SaaSRendererAdapter): void {
  globalSurfaceRegistry.registerAdapter(adapter);
}

export function resolveAdapter(uri: string): SaaSRendererAdapter | null {
  return globalSurfaceRegistry.resolveAdapter(uri);
}

export function unregisterAdapter(scheme: string, version?: number): void {
  globalSurfaceRegistry.unregisterAdapter(scheme, version);
}

export function markBroken(
  scheme: string,
  version: number,
  reason: string,
): void {
  globalSurfaceRegistry.markBroken(scheme, version, reason);
}

export function clearRegistry(): void {
  globalSurfaceRegistry.clearRegistry();
}

/**
 * @deprecated Use {@link registerAdapter}. Removed in Phase 4-a once
 * `EmailRenderer` and any other spike-prep consumer migrate to the
 * `SaaSRendererAdapter` contract (PRD D28).
 */
export function registerSurface(
  scheme: string,
  component: ComponentType<SurfaceRendererProps>,
): void {
  globalSurfaceRegistry.registerSurface(scheme, component);
}

/**
 * @deprecated Use {@link resolveAdapter}. Removed in Phase 4-a.
 */
export function resolveSurface(
  uri: string,
): ComponentType<SurfaceRendererProps> | null {
  return globalSurfaceRegistry.resolveSurface(uri);
}
