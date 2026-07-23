// Local dot-path resolver (Generative Surfaces v2, PRD-B2 D2).
//
// A deliberate ~40-line duplicate of `packages/surface-renderers/src/_shared/
// path.ts` (`resolvePath` / `isSafeHttpUrl`). We cannot import it: surface-
// renderers depends on chat-surface, so importing back would be a cycle
// (SDR §3 / package CLAUDE.md). The provenance selector uses this to resolve a
// `SurfaceLink.url_path` against a surface payload for the footer's deep link;
// the tests mirror the surface-renderers resolver cases so the two stay honest.

/**
 * Resolve a dotted accessor (`"a.b.0.c"`) against JSON-shaped data. Identifier
 * segments read mapping keys; all-digit segments index arrays (or numeric-keyed
 * objects). Returns `undefined` on any miss — a wrong path, a primitive
 * mid-traversal, a null hole — never throws. Iterative, so deep nesting costs
 * no stack.
 */
export function resolveDotPath(data: unknown, path: string): unknown {
  if (typeof path !== "string" || path.length === 0) {
    return undefined;
  }
  let current: unknown = data;
  for (const segment of path.split(".")) {
    if (current === null || current === undefined) {
      return undefined;
    }
    if (Array.isArray(current)) {
      if (!/^\d+$/.test(segment)) {
        return undefined;
      }
      current = current[Number(segment)];
      continue;
    }
    if (typeof current === "object") {
      current = (current as Record<string, unknown>)[segment];
      continue;
    }
    // A primitive with segments still to consume — dead end.
    return undefined;
  }
  return current;
}

/** True only for `http(s)://…` strings. Everything else (incl. `javascript:`,
 * `data:`, relative paths, non-strings) is unsafe — the footer omits the link
 * rather than render a broken/unsafe anchor (PRD-B2 D1 rule 5). */
export function isSafeHttpUrl(value: unknown): value is string {
  return typeof value === "string" && /^https?:\/\//i.test(value);
}
