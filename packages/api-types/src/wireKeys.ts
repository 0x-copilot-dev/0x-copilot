// Compile-enforced key sets for closed-key runtime guards.
//
// Several guards in this package are deliberately CLOSED: a payload carrying a
// field the guard does not know about is rejected outright, so a newly leaked
// server field can never reach a renderer unreviewed. That strictness is the
// point, and it is why the key list must never be written by hand.
//
// A hand-written `["a", "b", ...]` list has no relationship the compiler can
// check. Adding a field to the interface is then a compile-clean, test-clean
// change that makes the guard reject every real response at runtime.
//
// This is not hypothetical. `Artifact.accent` was added to the interface and
// its Python twin in a10c83a9 without touching `isArtifact`'s key list, so
// every `GET /v1/agent/artifacts/{id}` failed the guard and the desktop Studio
// canvas rendered no artifact at all. Nothing failed to compile, and the guard
// tests kept passing because their fixtures predated the field too.

/**
 * Derive a closed-key guard's runtime key set from a compiler-checked literal.
 *
 * The literal is checked against `Record<keyof T, true>`, which turns key drift
 * into a build error instead of a silent runtime rejection:
 *
 * - omitting a key that `T` declares  → TS2345, "Property 'accent' is missing
 *   in type ... but required in type 'Record<keyof Artifact, true>'"
 * - naming a key that `T` does not declare → TS2353 excess-property error
 *
 * Optional interface fields must be listed too. `Record<K, V>` does not carry
 * the `?` modifier over, so `accent?: SurfaceAccent` still requires
 * `accent: true` here — which is exactly right: the guard's job is to ALLOW the
 * key, whether or not a given response happens to carry it.
 *
 * Always pass the type argument explicitly, as `wireKeys<Artifact>({...})`.
 * `T` is not inferable from `keyof T`, so a bare `wireKeys({...})` collapses to
 * `Record<never, true>` and every key becomes an excess-property error — noisy,
 * but it fails loudly rather than silently accepting an unchecked list.
 */
export function wireKeys<T>(keys: Record<keyof T, true>): ReadonlySet<string> {
  return new Set(Object.keys(keys));
}
