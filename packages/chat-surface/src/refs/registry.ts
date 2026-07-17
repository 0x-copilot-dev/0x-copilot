// Cross-destination ItemRef resolver registry.
//
// Source: cross-audit.md §3.3 (binding 2026-05-17). One module-singleton
// indexed by `ItemKind`; each destination's `index.ts` registers its kind
// on package import. No React context — resolution happens outside the
// render tree (the `<ItemLink>` component pulls from it via a hook).
//
// Why not a React context: the registry needs to be set up at module
// init time, before any destination renders, AND it needs to be the
// same instance everywhere (a `<CommandPalette>` floats over Chats and
// must resolve Inbox refs). A module-singleton is the right shape; React
// context would force every consumer to be inside a provider boundary.

import type { ReactNode } from "react";

import type { ItemKind, ItemRef } from "@0x-copilot/api-types";

import type { ArtifactRoute } from "../routing/router";

/**
 * The resolved-display shape returned by a registered resolver.
 * Consumers (chiefly `<ItemLink>`) render an inline link from this.
 *
 * `route === null` is the deleted-item signal: the entity referenced
 * by the `ItemRef` no longer exists (or the caller doesn't have read
 * access). `<ItemLink>` renders a "deleted ${kind}" chip in that case
 * (cross-audit §5.3 cascade-on-delete default).
 */
export interface ItemRefResolved {
  readonly label: string;
  readonly icon: ReactNode;
  readonly route: ArtifactRoute | null;
  readonly breadcrumb?: string;
}

/**
 * Per-kind resolver function. Receives the correctly branded id (the
 * conditional type extracts the matching `ItemRef` branch's id type).
 * Returns the display shape, or `null` when the entity can't be
 * resolved at all (network error, transient unavailability) — distinct
 * from `route === null` which is the "exists-but-deleted" signal.
 */
export type ItemRefResolver<K extends ItemKind> = (
  id: ItemRef extends infer R
    ? R extends { kind: K; id: infer I }
      ? I
      : never
    : never,
) => Promise<ItemRefResolved | null>;

/**
 * Thrown by `registerItemRefResolver` when a resolver is already
 * registered for the kind and `replace: true` was not passed.
 *
 * Destinations register their resolver at package-import time; a
 * duplicate registration almost always means a destination was loaded
 * twice (test setup, hot-reload, double-provider). Throwing surfaces
 * the bug at boot rather than letting one resolver silently shadow
 * the other at render time.
 */
export class ItemRefResolverAlreadyRegistered extends Error {
  public readonly kind: ItemKind;

  constructor(kind: ItemKind) {
    super(
      `ItemRefResolverAlreadyRegistered: "${kind}" — pass { replace: true } to override deliberately`,
    );
    this.name = "ItemRefResolverAlreadyRegistered";
    this.kind = kind;
  }
}

/**
 * Thrown by `resolveItemRef` when no resolver has been registered for
 * the ref's kind. Destinations import their `index.ts` to register;
 * if a host renders an `<ItemLink>` whose kind hasn't been wired up,
 * we want the host's developer to see this loudly at first render —
 * not a silent "—" or a swallowed promise rejection.
 */
export class ItemRefResolverNotRegistered extends Error {
  public readonly kind: ItemKind;

  constructor(kind: ItemKind) {
    super(
      `ItemRefResolverNotRegistered: "${kind}" — import the owning destination's index.ts to register`,
    );
    this.name = "ItemRefResolverNotRegistered";
    this.kind = kind;
  }
}

// Module-singleton resolver table. Keyed by `ItemKind`. The value's
// generic is erased here (the public API restores it at the boundary)
// because TypeScript can't express "the resolver matches the key".
const REGISTRY = new Map<
  ItemKind,
  (id: string) => Promise<ItemRefResolved | null>
>();

/**
 * Register a resolver for one `ItemKind`. Throws
 * `ItemRefResolverAlreadyRegistered` on duplicate registration unless
 * `replace: true` is passed.
 *
 * Convention: destinations call this at module load (inside their
 * `index.ts`) so the registry is populated before any render.
 */
export function registerItemRefResolver<K extends ItemKind>(
  kind: K,
  resolver: ItemRefResolver<K>,
  options?: { readonly replace?: boolean },
): void {
  if (REGISTRY.has(kind) && options?.replace !== true) {
    throw new ItemRefResolverAlreadyRegistered(kind);
  }
  // Erase the per-kind id type at the storage boundary. The lookup-side
  // matches by `kind`, so the id we hand back is structurally correct.
  REGISTRY.set(
    kind,
    resolver as (id: string) => Promise<ItemRefResolved | null>,
  );
}

/**
 * Remove a registered resolver. Returns `true` if a resolver was
 * removed, `false` if none was registered. Intended for test cleanup
 * (`afterEach`) and hot-reload edge cases.
 */
export function unregisterItemRefResolver(kind: ItemKind): boolean {
  return REGISTRY.delete(kind);
}

/**
 * Clear all resolvers. Test-only helper; production code should never
 * call this.
 */
export function __resetItemRefRegistryForTests(): void {
  REGISTRY.clear();
}

/**
 * Look up the resolver for `ref.kind` and invoke it. Throws
 * `ItemRefResolverNotRegistered` when no resolver is registered for
 * the kind (so missing wiring is loud, not silent).
 */
export function resolveItemRef(ref: ItemRef): Promise<ItemRefResolved | null> {
  const resolver = REGISTRY.get(ref.kind);
  if (resolver === undefined) {
    return Promise.reject(new ItemRefResolverNotRegistered(ref.kind));
  }
  return resolver(ref.id);
}

/**
 * Inspect-only: is a resolver registered for `kind`? Used by tests
 * and by debug surfaces that want to render "unwired" placeholders
 * instead of throwing.
 */
export function hasItemRefResolver(kind: ItemKind): boolean {
  return REGISTRY.has(kind);
}
