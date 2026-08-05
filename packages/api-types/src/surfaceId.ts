// The one sanctioned way to obtain a `SurfaceId` (declared in ./brands.ts).
//
// A surface is a value produced once, by the backend `SurfaceProjector`, and
// transported whole. Its `surface_id` is simultaneously the canvas tab URI and
// the renderer mount key — one identity, no codec, no wrapper.
//
// These two functions exist so that crossing FROM an untrusted wire string INTO
// that identity is a single, greppable, validating step. Everything downstream
// holds the brand, so re-deriving an identity (a template literal, a
// percent-encode, a scheme swap) cannot type-check without coming back through
// here — which is the point.

import type { SurfaceId } from "./brands";

/** A URI encoded inside another URI: `record://wrapper/table%3A%2F%2Fa%2Fb`. */
const ENCODED_SCHEME_SEPARATOR = /%3a%2f%2f/i;

/**
 * Is `value` a single, unwrapped surface identity?
 *
 * The rule is deliberately about IDENTITY, not about scheme vocabulary: a
 * historic id predates the `<archetype>://<connector>/<tool>/<id>` form and
 * carries no `://` at all, and it is still exactly one identity. What is
 * rejected is an id that CONTAINS another id — a second `://`, or a
 * percent-encoded one — because that is the signature of a second identity
 * space being minted over the first, which is the defect this brand exists to
 * make unrepresentable.
 */
export function isSurfaceId(value: unknown): value is SurfaceId {
  if (typeof value !== "string" || value.length === 0) return false;
  if (ENCODED_SCHEME_SEPARATOR.test(value)) return false;
  return value.indexOf("://") === value.lastIndexOf("://");
}

/**
 * Assert `value` is a surface identity. Use at a boundary where a miss is a
 * programming error rather than untrusted input; prefer {@link isSurfaceId} when
 * folding a wire payload, where a malformed id must be skipped, not thrown on.
 */
export function asSurfaceId(value: string): SurfaceId {
  if (!isSurfaceId(value)) {
    throw new TypeError(
      `not a surface identity (an id may not contain another id): ${value}`,
    );
  }
  return value;
}
