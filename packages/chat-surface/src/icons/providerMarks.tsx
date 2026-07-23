// Provider brand marks — the bundled visual identity of a model provider.
//
// Two jobs, both of them offline:
//   1. `PROVIDER_BRAND_COLOR` — the hue of the 6px dot on the composer's model
//      pill (`.ui-cpill__dot`, whose background the consumer sets inline).
//   2. `<ProviderMark>` — the glyph inside a popover row's 24px badge
//      (`.ui-pop-row__lg`), or anywhere a provider needs a face.
//
// EVERYTHING IS AUTHORED LOCALLY. No `<img src>`, no favicon lookup, no runtime
// fetch of any kind. 0xCopilot is a local-first desktop app: a network icon
// would break offline AND leak which providers the user has configured to
// whoever serves the asset. A new provider ships as a path in THIS file, or it
// renders its initials — never as a URL.
//
// The marks are stylized, monochrome-capable interpretations drawn here — not
// vendored brand assets. `tone="current"` (the default) paints them in
// `currentColor` so a mark inherits the badge's text colour; `tone="brand"`
// paints the provider's own hue. Geometry follows the `<Icon>` conventions: one
// `0 0 24 24` viewBox, no per-path colour, the frame supplies every shared
// attribute (see `Icon.tsx` / `paths.tsx` — the line-icon SSOT).
//
// Providers WITHOUT a mark fall back to two-letter initials rather than to an
// invented logo: a wrong glyph in a brand slot is worse than honest text.

import type { CSSProperties, ReactElement, ReactNode } from "react";

import { ICON_PATHS } from "./paths";

/* ── brand colour ─────────────────────────────────────────────────────────── */

/**
 * Hue used for a provider's status dot. Seeded from the composer's own
 * `KEY_PROVIDER_DOT` map so the pill, the popover rows and any future surface
 * agree on one value.
 *
 * These are DATA, never the app accent — `--color-accent` stays the single
 * accent, and Anthropic's `#d97757` only coincidentally equals the rust accent
 * theme. Ollama is local/on-device software with no brand hue in this UI, so it
 * resolves to a neutral that flips with the theme (light llama on the dark
 * ground, dark llama on the light one).
 */
export const PROVIDER_BRAND_COLOR: Readonly<Record<string, string>> =
  Object.freeze({
    anthropic: "#d97757",
    openai: "#6aa88f",
    openrouter: "#9a7fd6",
    google: "#4285f4",
    ollama: "var(--color-text-strong)",
  });

/** Dot colour for a provider with no brand entry — the quiet neutral. */
export const PROVIDER_BRAND_COLOR_FALLBACK = "var(--color-text-muted)";

/** Brand hue for `provider`, or the neutral fallback. Case-insensitive. */
export function providerBrandColor(provider: string): string {
  const hue: string | undefined =
    PROVIDER_BRAND_COLOR[normalizeProvider(provider)];
  return hue ?? PROVIDER_BRAND_COLOR_FALLBACK;
}

/* ── initials fallback ────────────────────────────────────────────────────── */

// Display labels for the providers we know by slug, so `providerInitials("openrouter")`
// can answer "Or" (word initials) instead of "Op" (first two letters). Callers
// holding a richer catalogue should pass the LABEL — the rule below reads either.
const PROVIDER_LABEL: Readonly<Record<string, string>> = Object.freeze({
  anthropic: "Anthropic",
  openai: "OpenAI",
  openrouter: "OpenRouter",
  google: "Google",
  ollama: "Ollama",
});

function normalizeProvider(provider: string): string {
  return provider.trim().toLowerCase();
}

/**
 * Two-letter initials for a provider — the graceful fallback when no mark is
 * bundled. Accepts a slug (`"openrouter"`) or a display label (`"OpenRouter"`).
 *
 * The rule, in order:
 *   1. split into words on separators AND camelCase humps
 *      (`"OpenRouter"` → `Open|Router`, `"together-ai"` → `together|ai`);
 *   2. ignore fragments shorter than 3 characters when a longer word exists —
 *      that is what keeps the `AI` suffix out (`"OpenAI"` → `Op`, not `Oa`);
 *   3. two or more significant words → one letter each (`OpenRouter` → `Or`);
 *      otherwise the first two letters of the word (`Anthropic` → `An`).
 * Formatted `Xy` (leading capital) to sit on the badge like a monogram.
 *
 * Degenerate input is answered honestly: a single character yields one capital,
 * and empty / punctuation-only input yields `""` (there is nothing to derive —
 * `<ProviderMark>` renders `?` in that case rather than an empty box).
 */
export function providerInitials(provider: string): string {
  const known: string | undefined = PROVIDER_LABEL[normalizeProvider(provider)];
  const words = (known ?? provider).match(/[A-Z]+[a-z0-9]*|[a-z0-9]+/g) ?? [];
  const significant = words.filter((word) => word.length >= 3);
  const picked =
    significant.length >= 2
      ? `${significant[0]?.charAt(0) ?? ""}${significant[1]?.charAt(0) ?? ""}`
      : (significant[0] ?? words.join("")).slice(0, 2);
  if (picked === "") {
    return "";
  }
  return picked.charAt(0).toUpperCase() + picked.slice(1).toLowerCase();
}

/* ── marks ────────────────────────────────────────────────────────────────── */

interface MarkSpec {
  /** How the frame paints the geometry below. */
  readonly kind: "fill" | "stroke";
  /** Stroke weight for `kind: "stroke"` marks (each mark's own line weight). */
  readonly strokeWidth?: number;
  readonly body: ReactNode;
}

// One radial spoke of the Anthropic/Claude burst, repeated at 45° steps. The
// long/short alternation is what keeps it reading as a burst rather than as a
// plain asterisk.
function burstSpoke(angle: number, long: boolean): ReactElement {
  const height = long ? 18.6 : 14.4;
  return (
    <rect
      key={angle}
      x="11.1"
      y={(24 - height) / 2}
      width="1.8"
      height={height}
      rx="0.9"
      transform={angle === 0 ? undefined : `rotate(${angle} 12 12)`}
    />
  );
}

// One lobe of the OpenAI knot: a capsule through the centre, repeated at 60°
// steps so the three of them interleave into the six-fold blossom silhouette.
function knotLobe(angle: number): ReactElement {
  return (
    <rect
      key={angle}
      x="5.1"
      y="9.1"
      width="13.8"
      height="5.8"
      rx="2.9"
      transform={angle === 0 ? undefined : `rotate(${angle} 12 12)`}
    />
  );
}

/**
 * The bundled marks, by provider slug.
 *
 * Every entry is a stylized reading of the provider's mark, drawn here:
 * - `anthropic` — the Claude burst (eight tapered spokes, alternating length).
 * - `openai` — the six-fold knot, approximated as three interleaved capsules.
 * - `google` — the geometric G: a 320° ring with a bar into the centre.
 * - `ollama` — the design's OWN treatment for local on-device models: the chip
 *   glyph from the icon SSOT (`copilot-composer2.jsx` renders local rows with
 *   `<Icon.chip/>`, not a vendor logo), reused so there is one chip geometry.
 *
 * NOT here on purpose: `openrouter`. Its mark is not one this file can draw
 * faithfully, and a guessed logo in a brand slot is worse than honest text, so
 * it renders its "Or" initials. Drop a real path in when the asset is confirmed.
 */
const PROVIDER_MARKS: Readonly<Record<string, MarkSpec | undefined>> =
  Object.freeze({
    anthropic: {
      kind: "fill",
      body: (
        <>
          {burstSpoke(0, true)}
          {burstSpoke(45, false)}
          {burstSpoke(90, true)}
          {burstSpoke(135, false)}
        </>
      ),
    },
    openai: {
      kind: "stroke",
      strokeWidth: 1.8,
      body: (
        <>
          {knotLobe(0)}
          {knotLobe(60)}
          {knotLobe(120)}
        </>
      ),
    },
    google: {
      kind: "stroke",
      strokeWidth: 3,
      body: <path d="M18.09 6.51A8.2 8.2 0 1 0 20.2 12M12.4 12H20.2" />,
    },
    ollama: { kind: "stroke", strokeWidth: 1.7, body: ICON_PATHS.chip },
  });

/** Provider slugs that render a bundled mark (everything else → initials). */
export const PROVIDER_MARK_IDS: readonly string[] = Object.freeze(
  Object.keys(PROVIDER_MARKS),
);

/** Whether `provider` has a bundled mark. Case-insensitive. */
export function hasProviderMark(provider: string): boolean {
  return normalizeProvider(provider) in PROVIDER_MARKS;
}

/** `"current"` inherits the surrounding text colour; `"brand"` uses the hue. */
export type ProviderMarkTone = "current" | "brand";

export interface ProviderMarkProps {
  /** Provider slug (`"openai"`) or display label — both resolve. */
  readonly provider: string;
  /** Square px size. Default 13 — the design's badge glyph size. */
  readonly size?: number;
  /**
   * Display label used for the initials fallback when the provider is unknown.
   * Pass it when you have a catalogue entry (`"Together AI"` → `To`).
   */
  readonly label?: string;
  /** Default `"current"`: monochrome, so the mark sits on the badge fill. */
  readonly tone?: ProviderMarkTone;
  readonly className?: string;
  readonly style?: CSSProperties;
  /**
   * Accessible label. When set the mark becomes `role="img"` with this name;
   * when absent it is decorative (`aria-hidden`) and the row supplies the name.
   */
  readonly title?: string;
}

/**
 * A provider's face: the bundled mark when we have one, its two-letter
 * initials when we don't. Both branches occupy the same `size` box, so a list
 * of rows stays aligned whether or not every provider is known.
 */
export function ProviderMark({
  provider,
  size = 13,
  label,
  tone = "current",
  className,
  style,
  title,
}: ProviderMarkProps): ReactElement {
  const labelled = title !== undefined && title !== "";
  const tint = tone === "brand" ? providerBrandColor(provider) : undefined;
  const spec = PROVIDER_MARKS[normalizeProvider(provider)];

  if (spec === undefined) {
    const initials = providerInitials(label ?? provider);
    return (
      <span
        className={className}
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: size,
          height: size,
          fontSize: size * 0.72,
          lineHeight: 1,
          letterSpacing: "var(--tracking-normal)",
          color: tint,
          ...style,
        }}
        role={labelled ? "img" : undefined}
        aria-label={labelled ? title : undefined}
        aria-hidden={labelled ? undefined : true}
      >
        {initials === "" ? "?" : initials}
      </span>
    );
  }

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={spec.kind === "fill" ? "currentColor" : "none"}
      stroke={spec.kind === "stroke" ? "currentColor" : "none"}
      strokeWidth={spec.strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={tint === undefined ? style : { color: tint, ...style }}
      focusable={false}
      role={labelled ? "img" : undefined}
      aria-label={labelled ? title : undefined}
      aria-hidden={labelled ? undefined : true}
    >
      {spec.body}
    </svg>
  );
}
