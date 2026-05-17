import type { ComponentType, ReactElement } from "react";

import { TIER3_SCHEME, type SaaSRendererAdapter } from "./SaaSRendererAdapter";
import type { SurfaceRendererProps } from "./types";

interface RegistryEntry {
  adapter: SaaSRendererAdapter;
  broken: boolean;
}

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

export function registerAdapter(adapter: SaaSRendererAdapter): void {
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

export function resolveAdapter(uri: string): SaaSRendererAdapter | null {
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

export function unregisterAdapter(scheme: string, version?: number): void {
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

export function markBroken(
  scheme: string,
  version: number,
  _reason: string,
): void {
  const bucket = scheme === TIER3_SCHEME ? wildcard : exactScheme.get(scheme);
  if (!bucket) return;
  for (const entry of bucket) {
    if (entry.adapter.metadata.schemaVersion === version) {
      entry.broken = true;
      return;
    }
  }
}

export function clearRegistry(): void {
  exactScheme.clear();
  wildcard.length = 0;
  deprecatedComponents.clear();
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

/**
 * @deprecated Use {@link resolveAdapter}. Removed in Phase 4-a.
 */
export function resolveSurface(
  uri: string,
): ComponentType<SurfaceRendererProps> | null {
  const scheme = schemeFromUri(uri);
  if (scheme === null) return null;
  return deprecatedComponents.get(scheme) ?? null;
}
