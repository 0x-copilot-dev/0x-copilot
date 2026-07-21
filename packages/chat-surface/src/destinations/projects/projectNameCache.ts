// Project-name cache — feeds the `kind: "project"` ItemRef resolver.
//
// The resolver (registered in `./index.ts`) receives only a `ProjectId`;
// it has no transport and cannot fetch a name. Rather than shipping a
// bespoke fetch into `chat-surface` (which stays substrate-agnostic —
// no bare `fetch`/`window`), the host binder that already loads the
// project list (`apps/frontend .../ProjectsRoute.tsx`) primes this
// module-singleton cache with the `{ id, name }` pairs it fetched, and
// the resolver reads the real name back out. Miss → the resolver falls
// back to the generic "Project" label (FR-G.6).
//
// This mirrors the module-singleton shape of the resolver `REGISTRY`
// itself (`refs/registry.ts`): populated at data-load time, read at
// render time, the same instance everywhere. It is a plain `Map` — no
// browser primitive — so the package's port-clean invariant holds.

const PROJECT_NAMES = new Map<string, string>();

/**
 * Record one project's display name. No-op for empty names (an empty
 * label would be worse than the generic "Project" fallback).
 */
export function cacheProjectName(id: string, name: string): void {
  if (id.length > 0 && name.length > 0) {
    PROJECT_NAMES.set(id, name);
  }
}

/**
 * Prime the cache from a list of loaded projects. Call this from the
 * host binder whenever the project list (or a single project detail)
 * resolves, so cross-destination `<ItemLink kind="project">` links can
 * render the real name.
 */
export function cacheProjectNames(
  projects: Iterable<{ readonly id: string; readonly name: string }>,
): void {
  for (const project of projects) {
    cacheProjectName(project.id, project.name);
  }
}

/**
 * Look up a cached project name. `undefined` when unknown — the resolver
 * treats that as the "fall back to the generic label" signal.
 */
export function getCachedProjectName(id: string): string | undefined {
  return PROJECT_NAMES.get(id);
}

/** Test-only: clear the cache between suites. */
export function __resetProjectNameCacheForTests(): void {
  PROJECT_NAMES.clear();
}
