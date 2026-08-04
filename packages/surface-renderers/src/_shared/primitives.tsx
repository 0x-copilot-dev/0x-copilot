import type { CSSProperties, ReactElement, ReactNode } from "react";

import { Icon } from "@0x-copilot/chat-surface";

import { SURFACE_PALETTE as PALETTE } from "./palette";
import {
  formatValue,
  isNumericFormat,
  isSafeHttpUrl,
  numericValue,
  resolvePath,
} from "./path";
import type { SurfaceFieldFormat } from "./specTypes";

// Shared, pure presentational primitives for the ArchetypeRenderer pack
// (PRD-03). These reuse the visual grammar of the tier-1 renderers
// (OpportunityRenderer / EmailRenderer) so generic archetypes read as the same
// system. No interactivity, no globals, no I/O — D28.

export const SURFACE_FONT =
  "var(--font-sans, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif)";

/**
 * The mono face, for the IDENTIFIER register: a tool name, a raw payload key.
 * Those are things a machine emitted, not prose, and the design sets them in
 * mono so the eye can tell "what the tool called it" from "what we wrote".
 */
export const SURFACE_MONO_FONT =
  "var(--font-mono, ui-monospace, SFMono-Regular, 'JetBrains Mono', Menlo, monospace)";

/**
 * Two design-system rungs `SURFACE_PALETTE` has no name for. They are token
 * references, never hexes — the same discipline `palette.ts` enforces — and
 * they live here rather than in the palette because that file is deliberately
 * read-only for this change.
 *
 * `--color-bg-elevated` is the design's `--ink2`: a band RECESSED into the card
 * (`#0d0d10` under `--color-surface`'s `#111114`), which is what a note and a
 * footer are. `--color-text-subtle` is `--mut2`, one rung quieter than
 * `PALETTE.textLo` — the design deliberately sets the note's icon at `--mut`
 * and the field label at `--mut2`, and collapsing both to one rung would erase
 * a hierarchy it spends a token on.
 */
const BAND_BG = "var(--color-bg-elevated)";
const TEXT_SUBTLE = "var(--color-text-subtle)";

/* ==========================================================================
 * THE TYPE / SPACE / RADIUS RUNGS THIS FILE COMPOSES
 * ==========================================================================
 *
 * Everything below used to be a bare number — `fontSize: 11`,
 * `letterSpacing: 0.6`, `padding: 22`, `borderRadius: 14`, `fontWeight: 600`.
 * That is the same failure `palette.ts` fixed for colour, one tier over: a
 * private ladder inside a package that eleven renderers share, which no
 * density switch, no theme, and no design import can reach. `packages/
 * design-system/CLAUDE.md` states the rule those literals broke — "never
 * hard-code rems / pixels for font-size, never hard-code numeric font-weight
 * literals", and "letter-spacing never takes a raw em".
 *
 * TWO RULES, and they are not the same rule:
 *
 * 1. TYPE always resolves through a token. Family, size, weight, tracking and
 *    line-height have no exceptions here — the design's mono metadata register
 *    steps in HALF pixels (8.5 / 9.5 / 10 / 10.5), which is exactly why the
 *    mono micro-ladder exists and why reaching for a nearby sans rung is how a
 *    label shipped 18% too large. The `var(--token, <px>)` form carries the
 *    intended pixel as a fallback on purpose: this package renders inside three
 *    hosts, and a host that fails to load `design-system/src/styles.css` would
 *    otherwise resolve an unset custom property to the 16px INITIAL — a 9.5px
 *    mono label rendering at 16px is the loudest possible failure. The fallback
 *    is a floor, never the source of truth.
 *
 * 2. SPACE / RADIUS take a rung when one lands EXACTLY on the design's value,
 *    and stay a documented literal when none does. The surface language authors
 *    several of its gaps and paddings in odd pixels (7 / 9 / 14) that a
 *    2/4/8/12/16/24 ladder cannot express, and rounding them would move
 *    geometry that currently MATCHES the design in order to satisfy a token —
 *    the wrong trade, and not one the design system asks for: its own recipes
 *    keep the same odd values (`.ui-badge { gap: 5px; padding: 1px 8px }`,
 *    `.ui-input { padding: 7px 10px }`). Each surviving literal names the
 *    design rule it comes from, so the next reader can tell a considered
 *    number from a forgotten one.
 */

/** Mono micro-ladder. `.sfc-k`, `.sfr > .l` and `.sfbd-h` are all `--sf-lab`. */
const SIZE_MONO_9_5 = "var(--font-size-mono-9-5, 9.5px)";
/** `.sfb` (the badge) and `.sf-lnk` (the source link) — the ladder's top rung. */
const SIZE_MONO_10_5 = "var(--font-size-mono-10-5, 10.5px)";
/** `.sf-note code` — the tool identifier inside the note's sentence. */
const SIZE_11 = "var(--font-size-11, 11px)";
/** `.sf-note`, `.sf-bar .c`, `.sfc-s`, `.sfr > .v.n`. A whole-pixel rung the
 * rem ladder steps over (11.2 / 12.5); named for its px value, like
 * `--font-size-mono-10`. Its declaration scopes itself to the composer +
 * popover recipes because those were its only consumers when it landed — the
 * surface language authors the same whole pixels, and the alternative
 * (`--font-size-xs`, 12.5px) would break four rows that match the design today
 * to honour a comment. Recorded rather than silently widened. */
const SIZE_12 = "var(--font-size-12, 12px)";
/** `.sfr > .v` and the app's inherited body base. */
const SIZE_13 = "var(--font-size-sm, 13px)";
/** `.ui-item-title`'s rung. The design's `.sfc-t` is 15px and the sans ladder
 * has no 15px rung; `md` is the recipe that names this role ("Item / card / row
 * title"), and the design putting a surface title BELOW a section heading is
 * the same statement — a surface card is a compact artifact frame, not a page
 * section. */
const SIZE_ITEM_TITLE = "var(--font-size-md, 14px)";
/** `.ui-section-label` / `.ui-pill`'s rung — an uppercase micro-label. */
const SIZE_LABEL = "var(--font-size-2xs, 11.2px)";

const WEIGHT_SEMIBOLD = "var(--font-weight-semibold, 600)";
const WEIGHT_MEDIUM = "var(--font-weight-medium, 500)";

/** `.ui-mono-caps`' tracking — the 9.5px mono caps register. The design writes
 * 0.12em on `.sfc-k` and 0.11em (`--sf-tr`) on `.sfr > .l`; both land on this
 * rung, and 0.11 → 0.12em moves the label by 0.095px. */
const TRACK_MONO_CAPS = "var(--tracking-mono-caps, 0.12em)";
/** `.ui-section-label`'s tracking — the fold that 0.04–0.06em lands in. */
const TRACK_LABEL = "var(--tracking-label, 0.05em)";
/** `.ui-heading--3`'s tracking. `.sfc-t` carries -0.014em; `--tracking-normal`
 * would leave the title reporting `normal` against a design that tightens. */
const TRACK_SNUG = "var(--tracking-snug, -0.01em)";

/** `.sf-note`'s 1.55 — and it is the DENSITY-aware token, not the raw number,
 * so a compact workspace tightens the note with everything else. */
const LINE_BODY = "var(--line-height-body, 1.55)";

const SPACE_2XS = "var(--space-2xs, 2px)";
const SPACE_XS = "var(--space-xs, 4px)";
const SPACE_SM = "var(--space-sm, 8px)";
const SPACE_MD = "var(--space-md, 12px)"; // the design's `--sf-cx`
const SPACE_LG = "var(--space-lg, 16px)";
const SPACE_XL = "var(--space-xl, 24px)";

const RADIUS_MD = "var(--radius-md, 8px)";
const RADIUS_LG = "var(--radius-lg, 12px)"; // `.sfc`
const RADIUS_FULL = "var(--radius-full, 999px)";

export const pageStyle: CSSProperties = {
  background: PALETTE.pageBg,
  minHeight: "100%",
  padding: SPACE_XL,
  fontFamily: SURFACE_FONT,
  color: PALETTE.textHi,
  display: "flex",
  justifyContent: "center",
};

// NOTE on `padding` below: the parity row `card · padding` reads `0px → 24px`
// and does NOT close by changing this number. The design's `.sfc` declares no
// padding at all — its children (`.sfc-h`, `.sft-wrap`, `.sf-bar`) carry it,
// and we put it on the card. That is a STRUCTURAL difference, so 22px and 24px
// are equally open; the token is kept because tokenisation is the point.
export const cardStyle: CSSProperties = {
  background: PALETTE.surface,
  border: `1px solid ${PALETTE.border}`,
  // `.sfc` is 12px. The 14px this replaces predated the v3 import and was the
  // legacy sheet's own radius (`[data-lang="legacy"] .sfc`).
  borderRadius: RADIUS_LG,
  width: "100%",
  maxWidth: 820,
  display: "flex",
  flexDirection: "column",
  gap: SPACE_LG,
  // The design's card pads NOTHING (`.sfc { padding: 0 }`) because every band
  // inside it bleeds edge to edge; ours insets its bands instead, so the inset
  // lives here — see `noteStyle` for why that divergence is deliberate. 22px
  // was the legacy header's inset and is not on any rung; `xl` is the rung it
  // rounds to, 2px out.
  padding: SPACE_XL,
  boxShadow: "0 8px 28px rgba(0,0,0,0.4)",
};

const headerRowStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "flex-start",
  borderBottom: `1px solid ${PALETTE.border}`,
  paddingBottom: SPACE_MD,
  gap: SPACE_MD,
};

const headerTitleStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: SPACE_XS,
  minWidth: 0,
};

/**
 * `.sfc-k` — mono caps at the micro rung on `--mut2`, i.e. the `.ui-mono-caps`
 * recipe. It names the ARCHETYPE whose renderer ran ("Board", "Table"), which
 * is a machine's word for the view, not a title we wrote — the same reason the
 * canvas tab beside it is mono.
 *
 * It shipped as sans 11px on `--mut`: one whole register too loud, and 1.5px
 * too large. The dot inherits every one of these values (it paints no text), so
 * this declaration is also what the `card.kicker-dot` anchor measures.
 */
const kickerStyle: CSSProperties = {
  alignItems: "center",
  display: "flex",
  fontFamily: SURFACE_MONO_FONT,
  fontSize: SIZE_MONO_9_5,
  gap: 7, // `.sfc-k { gap: 7px }` — between the 4px and 8px rungs
  letterSpacing: TRACK_MONO_CAPS,
  color: TEXT_SUBTLE,
  textTransform: "uppercase",
};

/** `.sfc-t`. See {@link SIZE_ITEM_TITLE} for why this is the item-title rung
 * and not a heading one. */
const titleStyle: CSSProperties = {
  fontSize: SIZE_ITEM_TITLE,
  color: PALETTE.textHi,
  fontWeight: WEIGHT_SEMIBOLD,
  letterSpacing: TRACK_SNUG,
  overflowWrap: "anywhere",
};

/** `.sfc-s` — 12px on `--mut`, a step under the title in BOTH size and colour.
 * At 13px on `--tx2` it sat one rung under the title and one over the values,
 * which reads as a second title rather than as the title's aside. */
const subtitleStyle: CSSProperties = {
  fontSize: SIZE_12,
  color: PALETTE.textLo,
  overflowWrap: "anywhere",
};

/**
 * `.sfb` — the design's badge: mono at the top micro rung, on `--mut`, hugging
 * its text inside a full-radius hairline with NO fill.
 *
 * No `letterSpacing` and no `fontWeight`, deliberately and not by omission:
 * `.sfb` declares neither, so both inherit, and the design measures 400 with
 * `normal` tracking. Pinning either (`.ui-badge` pins 500) would manufacture a
 * difference against the thing this is copied from. The 0.4px tracking removed
 * here had no design source at all.
 */
const badgeStyle: CSSProperties = {
  alignItems: "center",
  background: "transparent",
  border: `1px solid ${PALETTE.border}`,
  borderRadius: RADIUS_FULL,
  color: PALETTE.textLo,
  display: "inline-flex",
  fontFamily: SURFACE_MONO_FONT,
  fontSize: SIZE_MONO_10_5,
  gap: 6, // `.sfb { gap: 6px }` — between the 4px and 8px rungs
  padding: `${SPACE_2XS} ${SPACE_SM}`,
  whiteSpace: "nowrap",
};

export interface SurfaceHeaderProps {
  readonly kicker: string;
  readonly title: string;
  readonly subtitle?: string;
  readonly badge?: string;
}

export function SurfaceHeader(props: SurfaceHeaderProps): ReactElement {
  const { kicker, title, subtitle, badge } = props;
  return (
    <header style={headerRowStyle} data-testid="surface-header">
      <div style={headerTitleStyle}>
        <span style={kickerStyle}>
          {/* The same dot the canvas tab shows, so a surface and the tab that
              opened it are visibly one object. Both read `--surface-src` from
              the nearest `data-surface-hue` ancestor — the card never resolves
              a colour, which is why one hue change moves both. Decorative: the
              kicker text already names the archetype. */}
          <span aria-hidden="true" className="sf-kicker__dot" />
          {kicker}
        </span>
        <span style={titleStyle} data-testid="surface-title">
          {title || "Untitled"}
        </span>
        {subtitle ? (
          <span style={subtitleStyle} data-testid="surface-subtitle">
            {subtitle}
          </span>
        ) : null}
      </div>
      {badge ? (
        <span style={badgeStyle} data-testid="surface-badge">
          {badge}
        </span>
      ) : null}
    </header>
  );
}

/* ==========================================================================
 * THE BADGE REGISTER — `format: "badge"` as a chip
 * ==========================================================================
 *
 * WHY THIS EXISTS. `format` is a purely VISUAL hint, and until now the renderers
 * spent nothing on this one: `formatValue`'s `badge` case returns the string
 * unchanged, and both archetypes had exactly two value registers — plain and
 * numeric. So a spec that correctly typed a status as a badge painted the same
 * undifferentiated grey as the title beside it. That is not a cosmetic gap: the
 * curated Linear / GitHub / Asana specs type `state.name` and `status` as
 * badges, and rung-0 inference independently types every low-cardinality token
 * the same way (`capabilities/surfaces/infer.py`), so the hint the backend works
 * hardest to get right was the one the client discarded.
 *
 * A chip is what makes a status legible AS a status — a closed vocabulary you
 * scan down a column, rather than prose you read.
 */

/**
 * The chip. It is the SAME chip as {@link badgeStyle} — the design's `.sfb`,
 * i.e. the design system's `.ui-badge`: mono at the top micro rung inside a
 * full-radius hairline with no fill — spread rather than re-declared, so the
 * header's chip and a value's chip can never drift apart. The token sheet names
 * this exact role on the size rung it takes (`--font-size-mono-10-5`, "status
 * chip"), which is the reason no new rung is invented here.
 *
 * ONE token differs, deliberately, and it is the colour. The header badge is
 * metadata ABOUT the card ("7 cards") and sits on `--mut`; this chip IS the
 * value. `--mut` is already what {@link fieldLabelStyle} carries, so painting
 * both on it would collapse the label/value hierarchy the row spends a rung on —
 * and would make the chip QUIETER than the plain text it replaces, which is the
 * opposite of the point. `--color-text-strong` is the rung between: still under
 * the 13px sans value beside it, still clearly above the label.
 */
const valueBadgeStyle: CSSProperties = {
  ...badgeStyle,
  color: PALETTE.textMid,
};

/**
 * Longest formatted value still painted as a chip.
 *
 * A chip is `white-space: nowrap` by design — it hugs one token — so a long
 * value inside one does not wrap, it widens the row that holds it. The cap is a
 * PRESENTATION decision and never a truncation: a value past it renders in the
 * ordinary value register, in full. That distinction is the whole reason the cap
 * is safe. The hint is untrusted — a spec arrives over the wire and
 * `specFromState` gates on two fields — so `badge` can legitimately land on a
 * paragraph; declining the chip is honest, whereas clipping the text to fit one
 * would delete payload that the plain register would have shown.
 *
 * 48 is twice what rung-0 inference will ever type as a badge (24 characters,
 * ≤2 words — `infer.py::_Limits`), which leaves room for a hand-authored spec's
 * longer state label without leaving room for prose.
 */
export const BADGE_MAX_CHARS = 48;

/**
 * Whether a formatted value may be painted as a chip.
 *
 * Blank is excluded for the same reason an over-long value is: an empty pill
 * asserts that there is a value here and then shows none, where the plain
 * register's blank cell simply says the field was absent — and an absent field
 * is the common case on real connector payloads, not the edge case.
 */
export function paintsAsChip(
  format: SurfaceFieldFormat | undefined,
  text: string,
): boolean {
  return (
    format === "badge" && text.trim() !== "" && text.length <= BADGE_MAX_CHARS
  );
}

/**
 * The chip itself, inert by construction: a `span`, never an anchor and never a
 * button.
 *
 * A badge is a presentational treatment of a value the TOOL returned, so making
 * it activatable would hand tool output a control on our surface. This package
 * carries exactly one sanctioned interactive element — `link.url_path`,
 * host-sanitised in {@link SurfaceLinkRow} — and the badge is deliberately not a
 * second one.
 *
 * `data-surface-format` is what lets one selector find every chip across
 * archetypes without knowing each archetype's own test-id grammar; the `testId`
 * stays per-site so a specific cell is still addressable.
 */
export function SurfaceValueBadge(props: {
  readonly value: string;
  readonly testId: string;
}): ReactElement {
  return (
    <span
      style={valueBadgeStyle}
      data-surface-format="badge"
      data-testid={props.testId}
    >
      {props.value}
    </span>
  );
}

const rowStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "150px 1fr",
  alignItems: "baseline",
  gap: SPACE_MD,
  paddingBlock: 6, // no rung between `--space-xs` (4px) and `--space-sm` (8px)
  borderBottom: `1px solid ${PALETTE.border}`,
};

/** The SPEC-DRIVEN row's label — a name a spec author wrote, which is why it
 * stays sans where `genericLabelStyle` (a key the tool chose) is mono. That
 * makes it the `.ui-section-label` role exactly: uppercase micro-label heading
 * a value. Taking the recipe's rungs rather than re-assembling them is what
 * stops this label drifting to a fourth tracking value. */
const fieldLabelStyle: CSSProperties = {
  color: PALETTE.textLo,
  fontSize: SIZE_LABEL,
  letterSpacing: TRACK_LABEL,
  textTransform: "uppercase",
  fontWeight: WEIGHT_SEMIBOLD,
};

const fieldValueStyle: CSSProperties = {
  color: PALETTE.textHi,
  fontSize: SIZE_13,
  overflowWrap: "anywhere",
};

const numericValueStyle: CSSProperties = {
  ...fieldValueStyle,
  fontVariantNumeric: "tabular-nums",
};

export interface FieldRowProps {
  readonly fieldKey: string;
  readonly label: string;
  readonly value: string;
  /**
   * The spec's visual hint, which is what CHOOSES the value register: `badge`
   * becomes a chip, `number` / `currency` the tabular-figure one. The value
   * itself arrives already formatted — the caller owns `formatValue` because it
   * owns the raw payload — so this is only ever consulted for presentation.
   */
  readonly format?: SurfaceFieldFormat;
  /**
   * Explicit override of the numeric register, for a caller that knows a value
   * is a magnitude without a spec saying so. Defaults to the format's own
   * answer, which is what every spec-driven caller wants.
   */
  readonly numeric?: boolean;
}

export function FieldRow(props: FieldRowProps): ReactElement {
  const { fieldKey, label, value, format } = props;
  const numeric = props.numeric ?? isNumericFormat(format);
  return (
    <div style={rowStyle} data-testid={`field-${fieldKey}`}>
      <span style={fieldLabelStyle}>{label}</span>
      {/* The chip is nested INSIDE the value slot rather than replacing it: the
          slot is the grid item (a chip put there directly would blockify and
          stretch the whole column instead of hugging its token), and keeping it
          also keeps `field-*-value` meaning the same thing in both registers. */}
      <span
        style={numeric ? numericValueStyle : fieldValueStyle}
        data-testid={`field-${fieldKey}-value`}
      >
        {paintsAsChip(format, value) ? (
          <SurfaceValueBadge value={value} testId={`field-${fieldKey}-badge`} />
        ) : (
          value || " "
        )}
      </span>
    </div>
  );
}

export const fieldGridStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: SPACE_2XS,
};

// A "Preparing view…" hint used to sit above the generic field list on every
// archetype's spec-less path. It is gone rather than merely unused, because it
// was a LOADING state and that path is not loading: an unmatched tool result is
// a finished, complete view of what the tool returned, and {@link NoSpecView}
// now says so.
//
// Nothing here kept a legitimate caller, and the reason is structural rather
// than editorial: this package cannot tell an in-flight spec from an absent
// one. `SurfaceState` is `{spec?, data}` — a missing `spec` is the ONLY signal
// a renderer gets, and it means the same thing whether generation is running,
// finished with no match, or never started. A hint keyed off that would be a
// guess painted as a fact. The moment a spec genuinely is in flight is visible
// one layer up, where `surface_spec_generated` is awaited: chat-surface holds
// the tier-2 pending slot (`Tier2Loader`'s `tier2-loader-pending`), and any
// pending affordance belongs there, with the signal, not here without it.

const linkRowStyle: CSSProperties = {
  paddingTop: 6, // no rung between `--space-xs` (4px) and `--space-sm` (8px)
};

/**
 * `.sf-lnk` — mono at the top micro rung. A `url_path` resolves to an address
 * a machine emitted, so it belongs in the same IDENTIFIER register as the tool
 * name, not in the 13px sans body register it shipped in.
 *
 * The COLOUR deliberately does not follow `.sf-lnk` to `--mut`. The design
 * carries "this is clickable" on four channels an inline style has none of — a
 * cursor, an 11px external-link glyph, and a `:hover` that lifts the colour to
 * the accent — and {@link SurfaceLinkRow} renders anything that is NOT a safe
 * http(s) URL as inert text in `--mut`. Painting the live anchor `--mut` too
 * would erase the only difference between a link we resolved and a value we
 * refused, on the one view whose whole job is to be honest about untrusted
 * payload. So the register moves and the accent stays.
 */
const linkStyle: CSSProperties = {
  color: PALETTE.lime,
  fontFamily: SURFACE_MONO_FONT,
  fontSize: SIZE_MONO_10_5,
  textDecoration: "none",
};

/** The refused half of the same row: same register, quieted to `--mut`, so the
 * two states differ by exactly the one thing that differs. */
const inertLinkStyle: CSSProperties = {
  ...linkStyle,
  color: PALETTE.textLo,
  overflowWrap: "anywhere",
};

/** Renders `link.url_path` as a real anchor only when it resolves to an
 * `http(s)` URL; anything else is inert text (D9 injection bound, PRD-03 AC3). */
export function SurfaceLinkRow(props: {
  readonly label: string;
  readonly value: unknown;
}): ReactElement {
  const { label, value } = props;
  if (isSafeHttpUrl(value)) {
    return (
      <div style={linkRowStyle}>
        <a
          style={linkStyle}
          href={value}
          rel="noreferrer noopener"
          data-testid="surface-link"
        >
          {label || value}
        </a>
      </div>
    );
  }
  return (
    <div style={linkRowStyle}>
      <span style={inertLinkStyle} data-testid="surface-link-text">
        {label || String(value ?? "")}
      </span>
    </div>
  );
}

const emptyStyle: CSSProperties = {
  fontSize: SIZE_13,
  color: PALETTE.textLo,
  paddingBlock: 6, // no rung between `--space-xs` (4px) and `--space-sm` (8px)
};

export function EmptyBody(props: {
  readonly children?: ReactNode;
}): ReactElement {
  return (
    <div style={emptyStyle} data-testid="surface-empty">
      {props.children ?? "No data to display."}
    </div>
  );
}

/** The maximum number of top-level entries the spec-less generic list paints. */
export const GENERIC_FIELD_CAP = 40;

function titleize(key: string): string {
  return key
    .replace(/[_-]+/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

// The design's `.sfr` grid: a 172px label rail against a `minmax(0, 1fr)`
// value column, baseline-aligned. This is the GENERIC register — a payload key
// the tool chose, not a label a spec author wrote — which is why it is not the
// spec-driven `rowStyle` above and must not be folded into it.
const genericRowStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "172px minmax(0, 1fr)",
  gap: 14, // `.sfr { gap: 14px }` — between the 12px and 16px rungs
  alignItems: "baseline",
  // `.sfr { padding: 9px var(--sf-cx) }`. The inline half IS a rung (`--sf-cx`
  // is 12px); the block half sits between 8px and 12px and stays a literal.
  padding: `9px ${SPACE_MD}`,
  borderBottom: `1px solid ${PALETTE.border}`,
};

// Mono caps at the micro rung — the `.ui-mono-caps` recipe. A raw payload key
// is an identifier; setting it in the same sans face as the value would claim
// it is prose we chose.
const genericLabelStyle: CSSProperties = {
  fontFamily: SURFACE_MONO_FONT,
  fontSize: SIZE_MONO_9_5,
  letterSpacing: TRACK_MONO_CAPS,
  textTransform: "uppercase",
  color: TEXT_SUBTLE,
  overflowWrap: "anywhere",
};

const genericValueStyle: CSSProperties = {
  fontSize: SIZE_13,
  color: PALETTE.textHi,
  minWidth: 0,
  overflowWrap: "anywhere",
};

// `.sfr > .v.n` — the numeric register. Mono + tabular figures so a column of
// generic values still lines up on the decimal even with no spec to declare
// which fields are numbers.
const genericNumericValueStyle: CSSProperties = {
  ...genericValueStyle,
  fontFamily: SURFACE_MONO_FONT,
  fontSize: SIZE_12,
  fontVariantNumeric: "tabular-nums",
};

const genericGridStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
};

// A line ABOUT the list rather than a row in it: the "showing 40 of N" cap, and
// the sentence an empty payload gets. Both are the same act — the note one band
// above promises "the payload as the tool sent it", and a silent truncation or
// a silently blank band would each make that promise false. Neither carries a
// hairline: a meta line is always the list's last child, and the hairline is a
// separator with nothing below it to separate.
const genericMetaStyle: CSSProperties = {
  padding: `9px ${SPACE_MD}`,
  fontSize: SIZE_12,
  color: TEXT_SUBTLE,
};

// `.sfr:last-child { border-bottom: 0 }`. The hairline is a SEPARATOR, and the
// last row has nothing below it to be separated from — keeping it would draw a
// second line right above the read-only footer's own top border.
const genericLastRowStyle: CSSProperties = {
  ...genericRowStyle,
  borderBottom: "none",
};

function GenericFieldRow(props: {
  readonly fieldKey: string;
  readonly label: string;
  readonly value: string;
  readonly numeric: boolean;
  readonly last?: boolean;
}): ReactElement {
  const { fieldKey, label, value, numeric, last } = props;
  return (
    <div
      style={last ? genericLastRowStyle : genericRowStyle}
      data-testid={`field-${fieldKey}`}
    >
      <span style={genericLabelStyle}>{label}</span>
      <span
        style={numeric ? genericNumericValueStyle : genericValueStyle}
        data-numeric={numeric ? "true" : "false"}
        data-testid={`field-${fieldKey}-value`}
      >
        {value || " "}
      </span>
    </div>
  );
}

/**
 * A compact label/value list over the top-level primitive entries of an
 * `unknown` payload — the spec-less fallback body. Nested objects/arrays are
 * summarised, never expanded, so it can't recurse into a hostile deep tree.
 *
 * A payload with nothing in it gets a SENTENCE, not a blank band. `{}` and `[]`
 * are what an empty result set looks like — a query that matched no rows is
 * ordinary tool output, not a broken one — and painting them as an empty gap
 * between the note and the footer is the "something went wrong" reading rule 04
 * forbids, arrived at by omission. `null` is the same case one step further: the
 * primitive row below would render a lone `VALUE` label with nothing after it,
 * which reads as a fact that got cut off rather than one that was never there.
 */
export function GenericFieldList(props: {
  readonly data: unknown;
}): ReactElement {
  const { data } = props;
  if (data === null || data === undefined) {
    return <GenericMetaLine testId="surface-generic-empty" copy={NO_PAYLOAD} />;
  }
  if (typeof data !== "object") {
    const text = formatValue(data);
    // A scalar that formats to nothing is `""` — a payload the tool sent, with
    // nothing in it. The row below would be a `VALUE` rail against a blank
    // column, so it takes the same sentence the empty containers get.
    if (text === "") {
      return (
        <GenericMetaLine testId="surface-generic-empty" copy={EMPTY_PAYLOAD} />
      );
    }
    return (
      <div style={genericGridStyle} data-testid="surface-generic-fields">
        <GenericFieldRow
          fieldKey="value"
          label="Value"
          value={text}
          numeric={numericValue(data) !== null}
          last
        />
      </div>
    );
  }
  const entries = Array.isArray(data)
    ? data.map((value, index) => [String(index), value] as const)
    : Object.entries(data as Record<string, unknown>);
  if (entries.length === 0) {
    return (
      <GenericMetaLine testId="surface-generic-empty" copy={EMPTY_PAYLOAD} />
    );
  }
  const shown = entries.slice(0, GENERIC_FIELD_CAP);
  const hidden = entries.length - shown.length;
  return (
    <div style={genericGridStyle} data-testid="surface-generic-fields">
      {shown.map(([key, value], index) => (
        <GenericFieldRow
          key={key}
          fieldKey={key}
          label={titleize(key)}
          value={summarise(value)}
          numeric={numericValue(value) !== null}
          last={hidden === 0 && index === shown.length - 1}
        />
      ))}
      {hidden > 0 ? (
        <div style={genericMetaStyle} data-testid="surface-generic-field-cap">
          Showing {shown.length} of {entries.length} top-level fields.
        </div>
      ) : null}
    </div>
  );
}

/**
 * Two states, told apart because they are not the same fact: the tool answered
 * with nothing at all, or it answered with a container holding nothing. Both
 * are stated flatly, in the past tense, as things the tool DID — a description
 * of a finished call, which is what keeps this a view rather than a diagnosis.
 */
const NO_PAYLOAD = "The tool returned no payload.";
const EMPTY_PAYLOAD = "The tool returned an empty payload.";

/** The list reduced to a single sentence about itself. Keeps the
 * `surface-generic-fields` wrapper: the body of a no-spec surface is always
 * this one slot, whatever ends up inside it. */
function GenericMetaLine(props: {
  readonly testId: string;
  readonly copy: string;
}): ReactElement {
  return (
    <div style={genericGridStyle} data-testid="surface-generic-fields">
      <div style={genericMetaStyle} data-testid={props.testId}>
        {props.copy}
      </div>
    </div>
  );
}

function summarise(value: unknown): string {
  if (Array.isArray(value)) {
    return `${value.length} item${value.length === 1 ? "" : "s"}`;
  }
  if (value !== null && typeof value === "object") {
    const count = Object.keys(value as Record<string, unknown>).length;
    return `{ ${count} field${count === 1 ? "" : "s"} }`;
  }
  return formatValue(value);
}

// ---- The no-spec view (PRD-02; its copy retired by generative-ui-floor §3.8)

/**
 * Longest tool identifier the note paints.
 *
 * Deliberately far tighter than `MAX_DISPLAY_CHARS` (2000): this string sits
 * INSIDE a sentence, and a 2000-character "identifier" would push the rest of
 * that sentence — the part that explains what the user is looking at — off the
 * card. A real tool name is `pagerduty.incident.read`-shaped; anything past 64
 * characters is not a name we need to show in full to be honest about which
 * tool it was.
 */
export const TOOL_NAME_MAX_CHARS = 64;

/**
 * The tool identifier as it may be PAINTED, or `undefined` when there is none
 * worth painting.
 *
 * Strings only. A tool name arrives from the same untrusted boundary as the
 * payload, so an object or a number here is not a name — it is noise, and
 * `String(noise)` on screen ("[object Object]", "null") would read as a bug in
 * the very view whose job is to look deliberate. Absent is the honest answer,
 * and the note has a sentence for it.
 */
function displayToolName(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const trimmed = value.trim();
  if (trimmed === "") {
    return undefined;
  }
  return trimmed.length > TOOL_NAME_MAX_CHARS
    ? `${trimmed.slice(0, TOOL_NAME_MAX_CHARS)}…`
    : trimmed;
}

/**
 * The unmatched tool's name, read defensively out of an `unknown` surface
 * state, or `undefined`.
 *
 * Read ONLY from the envelope, never from the payload. A `{spec?, data}` value
 * has an envelope layer the runtime wrote and a `data` layer the tool wrote;
 * the flat legacy shape has no envelope at all, so the whole value is payload.
 * Naming the tool out of a payload would let tool output claim its own
 * provenance — a webhook body with a `source` key would end up quoted, in a
 * register that says "this is what the system knows", as the tool we could not
 * match. Absent beats wrong, and the note has a sentence for absent.
 *
 * Both paths are then the contract's own `SurfaceSource` shape
 * (`{server, tool}`), never an invented key: `source.tool` is where a spec-less
 * envelope names its tool, and `spec.source.tool` covers the real case where a
 * spec DID ride along but failed `specFromState`'s minimal-shape gate — we
 * could not shape the payload with it, but it still records which tool this
 * was, and provenance is the one thing {@link NoSpecView} still states out
 * loud.
 *
 * `source` is optional on `SurfaceState` and always will be: every surface
 * emitted before the field existed carries none, so absent stays a first-class
 * answer rather than a fault. `resolvePath` never throws and returns
 * `undefined` on every miss.
 */
export function toolNameFromState(state: unknown): string | undefined {
  if (!isSurfaceEnvelopeShape(state)) {
    return undefined;
  }
  return (
    displayToolName(resolvePath(state, "source.tool")) ??
    displayToolName(resolvePath(state, "spec.source.tool"))
  );
}

/** The same envelope test `dataFromState` makes when it decides whether to
 * unwrap: a `data` (or `spec`) key is the only signal that anything here was
 * written by the runtime rather than by the tool. */
function isSurfaceEnvelopeShape(state: unknown): boolean {
  return (
    typeof state === "object" &&
    state !== null &&
    !Array.isArray(state) &&
    ("data" in state || "spec" in state)
  );
}

/**
 * The design's `.sf-note` — a recessed band stating why this surface looks the
 * way it does.
 *
 * Inset rather than bled to the card's edges: the design's card has no padding
 * and every one of its bands spans it edge-to-edge, ours pads all children
 * INCLUDING the header, and a band that ran past an inset header would read as
 * a misalignment rather than as the design's full-width rule.
 *
 * No `role`. This is not a status and not an alert — announcing it as either is
 * precisely the "something broke / something is loading" reading rule 04
 * forbids. It is body copy, and assistive tech reads it in document order like
 * the sentence it is.
 */
const noteStyle: CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: 9, // `.sf-note { gap: 9px }` — between the 8px and 12px rungs
  padding: `9px ${SPACE_MD}`,
  fontSize: SIZE_12,
  lineHeight: LINE_BODY,
  color: PALETTE.textMid,
  background: BAND_BG,
  border: `1px solid ${PALETTE.border}`,
  // The design's band has no radius because it bleeds to the card's edges and
  // the card clips it; an inset band needs one. `md` is a rung under the card's
  // `lg`, which is what a band nested inside a rounded card wants.
  borderRadius: RADIUS_MD,
};

/** `.sf-note svg` — 13px, one rung quieter than the copy it introduces. */
const noteIconStyle: CSSProperties = {
  flex: "none",
  marginTop: SPACE_2XS,
  color: PALETTE.textLo,
};

/**
 * `.sf-note code` — the IDENTIFIER register. The tool name is a machine's name
 * for itself, and mono is what separates it from the sentence we wrote around
 * it. `overflowWrap` is the second guard behind {@link TOOL_NAME_MAX_CHARS}: a
 * 64-character unbroken token still has to wrap inside the band rather than
 * widen the card.
 */
const noteCodeStyle: CSSProperties = {
  fontFamily: SURFACE_MONO_FONT,
  fontSize: SIZE_11,
  color: PALETTE.textHi,
  overflowWrap: "anywhere",
};

/** The design's `.sf-bar` — the card's closing band. */
const readOnlyBarStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 9, // `.sf-bar { gap: 9px }` — between the 8px and 12px rungs
  flexWrap: "wrap",
  padding: `9px ${SPACE_MD}`,
  background: BAND_BG,
  border: `1px solid ${PALETTE.border}`,
  borderRadius: RADIUS_MD, // see `noteStyle` — an inset band needs one
};

const readOnlyCopyStyle: CSSProperties = {
  fontSize: SIZE_12,
  color: PALETTE.textLo,
  minWidth: 0,
};

/** `.sf-bar .c` drops its second clause to `--mut2`: the claim is "read-only",
 * the reason is a quieter aside. */
const readOnlyReasonStyle: CSSProperties = {
  color: TEXT_SUBTLE,
};

/**
 * The tier-3 generic view: a provenance line, the payload as the tool sent it,
 * and the read-only footer.
 *
 * WHAT WAS DELETED HERE, AND WHY IT WAS DELETED RATHER THAN REWORDED. This band
 * used to open by announcing that the tool's output had matched no spec, naming
 * the tool inside that sentence. It is gone under the generative-UI floor PRD
 * (§3.8 / AC17) — and it is not quoted anywhere in this package, comments
 * included, because AC17 is enforced as a `grep -ri` gate over `packages/` that
 * a well-meaning recital of the old string would red. The objection to it was
 * never tone. It named an internal mechanism the reader has no stake in — a user
 * should never see the word "spec" — it framed a complete, finished view as a
 * failure, and by the end it was routinely FALSE: a curated spec was matched
 * backend-side and then dropped on the way to the screen, so the copy
 * apologised for a match we had actually made. It also closed with a promise
 * ("a spec will be generated and cached on the next call") about machinery the
 * user cannot see and, on every path that still reaches here, will not happen.
 * Our own host mount had already written down what the band did to a reader:
 * `TcSurfaceMount` refuses to delegate its empty state to this tier because the
 * card "reads to a user like an error".
 *
 * WHY THE COMPONENT SURVIVED THE COPY. Rung 0 — deterministic inference — now
 * answers for every mapping-shaped tool output with no model and no failure
 * mode, so "the runtime produced a mapping payload and no spec" is no longer a
 * state the runtime can reach. Three other paths still reach it, and each is a
 * legitimate finished view rather than a fault:
 *
 *  1. REPLAY of a run recorded before the floor landed. Those events carry no
 *     spec and never will, and the transcript still has to render.
 *  2. A spec DROPPED at the transport allow-list. `surface.created` is rebuilt
 *     field by field and its spec re-validated before it is persisted; one that
 *     fails is discarded by design, because a wrong shaping is worse than none.
 *  3. A NON-MAPPING payload — a bare scalar or array. Inference declines it
 *     because there is no record and no table in it to find.
 *
 * So the rule this view now follows: **if we cannot shape the data, render the
 * data.** Legibly, as a finished thing. The tool name stays, as PROVENANCE —
 * which call produced what is on screen — never as an excuse for it. Nothing
 * here is a status, an alert or a loading state (design rule 04: "Unknown
 * degrades. Nothing in this pane ever renders as an error."); the note says
 * where this came from and what it is, and the footer says what cannot happen
 * here at all.
 *
 * The export name and the `surface-no-spec-*` test ids are deliberately
 * UNCHANGED. They are developer-facing, they still describe precisely when this
 * branch runs — every archetype selects it with `spec ? … : …` — and they are
 * read from outside this package by `tools/design-parity`'s anchor set. What
 * was false was the claim made to a user, and that is the thing that went.
 *
 * This is the degradation target for EVERY archetype — `TableRenderer`,
 * `RecordRenderer`, `BoardRenderer`, `DocRenderer` and `MessageRenderer` all
 * land here when `specFromState` returns `undefined` — so it is total over
 * `unknown`: `data` goes to {@link GenericFieldList}, which summarises rather
 * than recurses, and `tool` is gated by {@link displayToolName}.
 */
export function NoSpecView(props: {
  readonly data: unknown;
  /** Untrusted — see {@link displayToolName}. Absent whenever the envelope
   * carried no provenance, which every pre-`source` surface does. */
  readonly tool?: unknown;
}): ReactElement {
  const tool = displayToolName(props.tool);
  return (
    <>
      <div style={noteStyle} data-testid="surface-no-spec-note">
        <Icon name="eye" size={13} style={noteIconStyle} />
        <span>
          {/* A caption, not a sentence about a failure: it states what the band
              below IS and where it came from, in the past tense, as a finished
              act. The two clauses after the dash are the only honest limits
              this view has, and they are kept because dropping them would make
              the promise this line makes ("the payload as the tool sent it")
              false for a nested payload — `genericMetaStyle` is built around
              that promise holding. */}
          The payload as{" "}
          {tool === undefined ? (
            // Never "undefined" on screen, and never an empty gap where a name
            // should be: with no name to give, the caption names the THING
            // instead and stays true.
            "the tool"
          ) : (
            // Inert text in a code register — never an anchor. `url_path` on a
            // spec is the one sanctioned link on a surface, and this tier has
            // no spec by definition (PRD-02 requirement 5 + non-goals).
            <code style={noteCodeStyle} data-testid="surface-no-spec-tool">
              {tool}
            </code>
          )}{" "}
          sent it — top-level fields only, nested objects summarised.
        </span>
      </div>
      <GenericFieldList data={props.data} />
      {/* A SAFETY statement, not decoration: the whole point of this tier is
          that nothing here was understood well enough to act on, so the surface
          says out loud that it offers no way to act. */}
      <footer style={readOnlyBarStyle} data-testid="surface-read-only-footer">
        <span style={readOnlyCopyStyle}>
          Read-only.{" "}
          <span style={readOnlyReasonStyle}>
            Generic views never carry a write action.
          </span>
        </span>
      </footer>
    </>
  );
}

// ---- Diff primitives (reuse the OpportunityFieldRow grammar) --------------

const changedRowStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6, // `.sfk { gap: 6px }` — between the 4px and 8px rungs
  paddingBlock: SPACE_SM,
  paddingInline: 10, // between the 8px and 12px rungs
  borderRadius: RADIUS_MD,
  background: PALETTE.limeBgSoft,
  border: `1px solid ${PALETTE.lime}`,
  marginBlock: SPACE_2XS,
};

const changeHeaderStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: SPACE_MD,
  flexWrap: "wrap",
};

/** The `.ui-pill` role — "the canonical rounded status/selection chip: a
 * leading dot + tone variants" is this element exactly. It takes the recipe's
 * size and weight; the tracking comes from `.ui-section-label` instead, because
 * this pill's label is UPPERCASE and `--tracking-normal` (what `.ui-pill`
 * carries, for sentence-case chips) would set caps solid. `--tracking-label` is
 * the fold the 0.4px literal it replaces already landed in. */
const provenancePillStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6, // `.sfb { gap: 6px }` — between the 4px and 8px rungs
  padding: `${SPACE_2XS} ${SPACE_SM}`,
  borderRadius: RADIUS_FULL,
  border: `1px solid ${PALETTE.border}`,
  fontSize: SIZE_LABEL,
  fontWeight: WEIGHT_MEDIUM,
  letterSpacing: TRACK_LABEL,
  color: PALETTE.textLo,
  textTransform: "uppercase",
};

const provenanceDotStyle: CSSProperties = {
  display: "inline-block",
  width: 6,
  height: 6,
  borderRadius: "50%",
  background: PALETTE.lime,
};

const diffPairStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: SPACE_SM,
  flexWrap: "wrap",
  fontSize: SIZE_13,
};

const previousValueStyle: CSSProperties = {
  color: PALETTE.textLo,
  textDecoration: "line-through",
  textDecorationColor: PALETTE.textLo,
  overflowWrap: "anywhere",
};

const arrowStyle: CSSProperties = {
  color: PALETTE.textMid,
  fontSize: SIZE_12,
};

const nextValueStyle: CSSProperties = {
  color: PALETTE.textHi,
  background: "color-mix(in srgb, var(--color-accent) 18%, transparent)",
  // `.sfn { padding: 1px 5px; border-radius: 4px }` — a chip that hugs one
  // value inside a line of text. Every number here is under the ladder's
  // smallest rung (`--space-2xs` 2px / `--radius-sm` 6px); rounding up would
  // make the highlight taller than the line it sits in.
  padding: "1px 6px",
  borderRadius: 4,
  overflowWrap: "anywhere",
};

export interface DiffFieldRowProps {
  readonly fieldKey: string;
  readonly label: string;
  readonly previousValue: string;
  readonly nextValue: string;
  readonly provenance?: string;
}

/** A single before→after change row: struck-through old, accent-highlighted
 * new, provenance pill — the shared diff grammar for every archetype. */
export function DiffFieldRow(props: DiffFieldRowProps): ReactElement {
  const { fieldKey, label, previousValue, nextValue, provenance } = props;
  return (
    <div
      style={changedRowStyle}
      data-testid={`field-${fieldKey}`}
      data-changed="true"
    >
      <div style={changeHeaderStyle}>
        <span style={fieldLabelStyle}>{label}</span>
        <span
          style={provenancePillStyle}
          data-testid={`field-${fieldKey}-provenance`}
        >
          <span aria-hidden="true" style={provenanceDotStyle} />
          {provenance ?? "Proposed"}
        </span>
      </div>
      <div style={diffPairStyle}>
        <span
          style={previousValueStyle}
          data-testid={`field-${fieldKey}-previous`}
        >
          {previousValue || " "}
        </span>
        <span aria-hidden="true" style={arrowStyle}>
          →
        </span>
        <span style={nextValueStyle} data-testid={`field-${fieldKey}-next`}>
          {nextValue || " "}
        </span>
      </div>
    </div>
  );
}
