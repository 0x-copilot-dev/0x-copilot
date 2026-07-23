// Cross-destination ItemRef ROUTE registry (PRD-04 Seam B).
//
// History: this registry used to conflate two facts it could not know — an
// entity's DISPLAY NAME (per-entity, data-dependent, always already loaded by
// the surface rendering the list) and the HOST's ROUTE (expressible only in the
// host's own route union, which chat-surface deliberately does not depend on).
// Because it was kind-level, substrate-agnostic, and asynchronous with no data
// source, every implementation degenerated to a constant noun ("Run", "Chat",
// …). PRD-04 splits the two:
//
//   * Display text is now the caller's job — the ItemLink component takes it as
//     a required prop (Seam A).
//   * This registry narrows to ROUTING ONLY, and becomes SYNCHRONOUS. It maps
//     an `ItemKind` to a HOST route value. The return type is `unknown` because
//     the route belongs to the host's union (web `AppRoute`, desktop
//     `ArtifactRoute`); `<ItemLink>` hands it straight to `router.navigate`.
//
// Registration moves OUT of the chat-surface destinations and INTO one table
// per host (`apps/frontend/src/app/itemRoutes.ts`,
// `apps/desktop/renderer/itemRoutes.ts`), imported at boot. That is what makes
// the old web `/settings#undefined` bug structurally impossible: the web table
// can only emit `AppRoute`s, checked by `tsc`.
//
// A kind with NO registered route is not an error and not a "deleted" signal —
// it renders as inert, non-navigable text (`<ItemLink>` falls back to a plain
// <span>). "not navigable yet" and "deleted" are different facts; the old code
// reported both as deletion.
//
// Still a module-singleton (not a React context): the registry must be set up
// at boot, before any destination renders, and must be the same instance
// everywhere (a floating ⌘K palette over Chats must resolve Inbox routes).

import type { ItemKind, ItemRef } from "@0x-copilot/api-types";

/**
 * Per-kind route resolver. Receives the entity id and returns a HOST route
 * value (`AppRoute` on web, `ArtifactRoute` on desktop) — typed as `unknown`
 * here because chat-surface does not depend on either host union. `<ItemLink>`
 * passes it straight to `router.navigate(route)`. Returning `null` means "no
 * route for this id" → `<ItemLink>` renders inert text.
 */
export type ItemRouteResolver = (id: string) => unknown | null;

/**
 * Thrown by `registerItemRoute` when a route resolver is already registered for
 * the kind and `replace: true` was not passed. A duplicate registration almost
 * always means a host table was imported twice (test setup, hot-reload) — throw
 * at boot rather than let one resolver silently shadow the other.
 */
export class ItemRouteAlreadyRegistered extends Error {
  public readonly kind: ItemKind;

  constructor(kind: ItemKind) {
    super(
      `ItemRouteAlreadyRegistered: "${kind}" — pass { replace: true } to override deliberately`,
    );
    this.name = "ItemRouteAlreadyRegistered";
    this.kind = kind;
  }
}

/**
 * Thrown by `resolveItemRoute` when no route resolver has been registered for
 * the ref's kind. Callers that want the inert-text fallback must gate on
 * `hasItemRoute(kind)` first (as `<ItemLink>` does); this error exists so a
 * direct `resolveItemRoute` on an unwired kind is loud rather than silent.
 */
export class ItemRouteNotRegistered extends Error {
  public readonly kind: ItemKind;

  constructor(kind: ItemKind) {
    super(
      `ItemRouteNotRegistered: "${kind}" — register it in the host's itemRoutes table`,
    );
    this.name = "ItemRouteNotRegistered";
    this.kind = kind;
  }
}

// Module-singleton route table. Keyed by `ItemKind`.
const REGISTRY = new Map<ItemKind, ItemRouteResolver>();

/**
 * Register a route resolver for one `ItemKind`. Throws
 * `ItemRouteAlreadyRegistered` on duplicate registration unless
 * `replace: true` is passed. Hosts call this once at boot (from their
 * `itemRoutes` table).
 */
export function registerItemRoute(
  kind: ItemKind,
  resolve: ItemRouteResolver,
  options?: { readonly replace?: boolean },
): void {
  if (REGISTRY.has(kind) && options?.replace !== true) {
    throw new ItemRouteAlreadyRegistered(kind);
  }
  REGISTRY.set(kind, resolve);
}

/**
 * Remove a registered route resolver. Returns `true` if one was removed,
 * `false` if none was registered. Intended for test cleanup and hot-reload.
 */
export function unregisterItemRoute(kind: ItemKind): boolean {
  return REGISTRY.delete(kind);
}

/**
 * Clear all route resolvers. Test-only helper; production code should never
 * call this.
 */
export function __resetItemRouteRegistryForTests(): void {
  REGISTRY.clear();
}

/**
 * Resolve `ref` to a HOST route value, synchronously. Throws
 * `ItemRouteNotRegistered` when no resolver is registered for the kind — gate
 * on `hasItemRoute(ref.kind)` first if you want the inert-text fallback instead
 * (that is exactly what `<ItemLink>` does).
 */
export function resolveItemRoute(ref: ItemRef): unknown | null {
  const resolve = REGISTRY.get(ref.kind);
  if (resolve === undefined) {
    throw new ItemRouteNotRegistered(ref.kind);
  }
  return resolve(ref.id);
}

/**
 * Is a route resolver registered for `kind`? `<ItemLink>` consults this to
 * decide between an interactive `<a>` and inert `<span>` text.
 */
export function hasItemRoute(kind: ItemKind): boolean {
  return REGISTRY.has(kind);
}
