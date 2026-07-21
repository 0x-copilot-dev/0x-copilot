/**
 * React scoping for the surface-adapter registry (PRD-11 groundwork).
 *
 * A subtree wrapped in {@link SurfaceRegistryProvider} resolves surface adapters
 * against the provided (isolated) registry; everything else resolves against the
 * process-wide {@link globalSurfaceRegistry}. The context default IS the global
 * instance, so {@link useSurfaceRegistry} returns the global when no provider is
 * present — the free registry functions and the default-context reads observe
 * the same state. This is groundwork only: it enables per-tenant scoping for
 * multi-tenant web later; the desktop single-instance default is unchanged.
 */
import { createContext, useContext, type ReactNode } from "react";

import { globalSurfaceRegistry, type SurfaceRegistry } from "./SurfaceRegistry";

const SurfaceRegistryContext = createContext<SurfaceRegistry>(
  globalSurfaceRegistry,
);

export interface SurfaceRegistryProviderProps {
  /** The registry this subtree resolves against, from `createSurfaceRegistry()`. */
  readonly registry: SurfaceRegistry;
  readonly children: ReactNode;
}

/**
 * Scope a subtree to an isolated {@link SurfaceRegistry}. Consumers below it
 * (notably `TcSurfaceMount`) resolve adapters against `registry` instead of the
 * global.
 */
export function SurfaceRegistryProvider(
  props: SurfaceRegistryProviderProps,
): ReactNode {
  return (
    <SurfaceRegistryContext.Provider value={props.registry}>
      {props.children}
    </SurfaceRegistryContext.Provider>
  );
}

/**
 * The registry the current subtree should resolve against: the nearest
 * {@link SurfaceRegistryProvider}'s instance, or {@link globalSurfaceRegistry}
 * when there is none (the default).
 */
export function useSurfaceRegistry(): SurfaceRegistry {
  return useContext(SurfaceRegistryContext);
}
