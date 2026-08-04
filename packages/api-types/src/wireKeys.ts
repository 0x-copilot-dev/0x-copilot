// Compile-enforced key sets for the closed-key runtime guards in this package.
//
// Several guards here are deliberately CLOSED: a payload carrying a field the
// guard does not know about is rejected outright, so a newly leaked server
// field can never reach a renderer unreviewed. That strictness is the point,
// and it is exactly why the key list must never be written by hand.

/**
 * The wire keys of `T`, as a set the compiler forces to stay complete.
 *
 * A closed `hasOnlyKeys` check rejects the WHOLE response over a field the
 * server already serves but the guard has not enumerated. That is not a
 * theoretical risk — `accent` was added to `Artifact` (and to its Python twin)
 * in a10c83a9 without being added to `isArtifact`'s key list, and from then on
 * every `GET /v1/agent/artifacts/{id}` failed the guard, so the Studio canvas
 * never left its loading placeholder for any artifact of any kind. Nothing
 * failed to compile, and the guard tests stayed green because their fixtures
 * predated the field too.
 *
 * Typing the source as `Record<keyof T, true>` makes the omission a build
 * failure instead:
 *
 * - omit a key `T` declares  → TS2345, "Property 'accent' is missing in type
 *   ... but required in type `Record<keyof Artifact, true>`"
 * - name a key `T` does not declare → TS2353 excess-property error
 *
 * Prefer this over a bare `new Set([...])` for every closed-key guard — the set
 * literal is exactly what drifted.
 *
 * Optional interface fields must be listed too. `Record<K, V>` does not carry
 * the `?` modifier over, so `accent?: SurfaceAccent` still requires
 * `accent: true` here — which is right: the guard's job is to ALLOW the key,
 * whether or not a given response happens to carry it.
 *
 * Always pass the type argument explicitly, as `wireKeys<Artifact>({...})`.
 * `T` is not inferable from `keyof T`, so a bare `wireKeys({...})` collapses to
 * `Record<never, true>` and every key becomes an excess-property error — noisy,
 * but it fails loudly rather than silently accepting an unchecked list.
 */
export function wireKeys<T>(keys: Record<keyof T, true>): ReadonlySet<string> {
  return new Set(Object.keys(keys));
}
