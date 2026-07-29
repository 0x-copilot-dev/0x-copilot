// The IDENTITY register of the Studio surface language (design: "0xCopilot
// Surface Language").
//
// A generative surface says which source it came from with a hue, and nothing
// else: one perceptual ring where every entry is oklch(0.76 0.1 H), so hue
// alone distinguishes a table from a record from a board. The colour values
// live in `packages/design-system/src/styles.css` under SOURCE-HUE RING; this
// module owns only the NAME, and only the mapping from a surface URI to it.
//
// Why a name and not a colour. Two reasons, and both are load-bearing:
//
//  1. The model may choose a surface's hue (`publish_artifact`). It picks from
//     this closed set of names; it never sends a colour, a hex, or CSS. That
//     keeps the architecture's standing rule — the model emits data and intent,
//     the renderer paints — intact for colour exactly as it is for layout.
//  2. A name survives a theme flip. The light ground needs a different
//     lightness for the same identity, which a resolved colour cannot express.
//
// Consumers set `data-surface-hue={hue}` on a container and read `--surface-src`
// downstream. No renderer resolves a hue to a colour itself.

/** The closed set of source hues. `none` is the honest absence of identity. */
export type SurfaceHue =
  | "jade"
  | "sky"
  | "indigo"
  | "ember"
  | "violet"
  | "plum"
  | "amber"
  | "none";

/** Runtime SSOT tuple — mirrors the `[data-surface-hue]` blocks in styles.css. */
export const SURFACE_HUES = [
  "jade",
  "sky",
  "indigo",
  "ember",
  "violet",
  "plum",
  "amber",
  "none",
] as const satisfies readonly SurfaceHue[];

const HUE_SET: ReadonlySet<string> = new Set(SURFACE_HUES);

/** Narrow an untrusted value (a model choice, a stored record) to a hue. */
export function isSurfaceHue(value: unknown): value is SurfaceHue {
  return typeof value === "string" && HUE_SET.has(value);
}

/**
 * Scheme → hue. The design fixes six of these directly; the rest follow its
 * grouping rule, which is that the hue tracks WHAT KIND OF THING the surface
 * is, not which renderer happens to paint it.
 *
 * Two groupings are worth stating because they look like mistakes and are not:
 *
 *  - `artifact-dataset` is sky, NOT table's jade, even though both render as a
 *    grid. A dataset artifact is something the run produced and owns; a
 *    `table://` is a read of somebody else's system. Different provenance,
 *    different identity — the design separates them deliberately.
 *  - `doc` (300) and `board` (288) sit 12° apart, close enough to read as one
 *    family at dot scale. That is the design's intent, not an oversight: both
 *    are tier-2 "generated · cached" surfaces and share a violet register.
 *
 * Anything unmapped resolves to `none` — a hollow ring. An unknown source has
 * no identity to show, and borrowing one would be a lie.
 */
const HUE_BY_SCHEME: Readonly<Record<string, SurfaceHue>> = {
  // Reads of an external system — the design's curated-spec archetypes.
  table: "jade",
  record: "indigo",
  "sf-opp": "indigo",
  message: "ember",
  email: "ember",
  doc: "violet",
  slide: "violet",
  board: "plum",
  "sheet-row": "sky",
  // Artifacts the run itself produced.
  "artifact-dataset": "sky",
  "artifact-document": "violet",
  "artifact-code": "amber",
  // A file artifact is opaque bytes. There is no shape to have an identity
  // about, so it stays hollow rather than claiming a source it does not have.
  "artifact-file": "none",
};

/** The scheme of `scheme://rest`, or `null` when the string is not a URI. */
function schemeOf(uri: string): string | null {
  const separator = uri.indexOf("://");
  if (separator <= 0) return null;
  return uri.slice(0, separator).toLowerCase();
}

/**
 * The hue a surface URI carries on its own, before any explicit choice. Total:
 * an unparseable or unmapped URI is `none`, never a throw and never a default
 * colour that would make one source impersonate another.
 */
export function surfaceHueForUri(uri: string): SurfaceHue {
  const scheme = schemeOf(uri);
  if (scheme === null) return "none";
  return HUE_BY_SCHEME[scheme] ?? "none";
}

/**
 * The hue to render, given a URI and an optional explicit choice.
 *
 * The choice wins when it is a valid hue — that is the seam through which a
 * `publish_artifact` accent reaches the tab strip and the card. An invalid or
 * absent choice falls back to the URI's own hue rather than to `none`, so a
 * malformed model argument degrades to the correct default instead of
 * stripping an artifact of the identity its kind already implies.
 */
export function resolveSurfaceHue(input: {
  readonly uri: string;
  readonly choice?: unknown;
}): SurfaceHue {
  return isSurfaceHue(input.choice)
    ? input.choice
    : surfaceHueForUri(input.uri);
}
