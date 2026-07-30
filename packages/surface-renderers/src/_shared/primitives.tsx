import type { CSSProperties, ReactElement, ReactNode } from "react";

import { Icon } from "@0x-copilot/chat-surface";

import { SURFACE_PALETTE as PALETTE } from "./palette";
import { formatValue, isSafeHttpUrl, numericValue, resolvePath } from "./path";

// Shared, pure presentational primitives for the ArchetypeRenderer pack
// (PRD-03). These reuse the visual grammar of the tier-1 renderers
// (OpportunityRenderer / EmailRenderer) so generic archetypes read as the same
// system. No interactivity, no globals, no I/O — D28.

export const SURFACE_FONT =
  "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";

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

export const pageStyle: CSSProperties = {
  background: PALETTE.pageBg,
  minHeight: "100%",
  padding: 24,
  fontFamily: SURFACE_FONT,
  color: PALETTE.textHi,
  display: "flex",
  justifyContent: "center",
};

export const cardStyle: CSSProperties = {
  background: PALETTE.surface,
  border: `1px solid ${PALETTE.border}`,
  borderRadius: 14,
  width: "100%",
  maxWidth: 820,
  display: "flex",
  flexDirection: "column",
  gap: 16,
  padding: 22,
  boxShadow: "0 8px 28px rgba(0,0,0,0.4)",
};

const headerRowStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "flex-start",
  borderBottom: `1px solid ${PALETTE.border}`,
  paddingBottom: 12,
  gap: 12,
};

const headerTitleStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
  minWidth: 0,
};

const kickerStyle: CSSProperties = {
  alignItems: "center",
  display: "flex",
  fontSize: 11,
  gap: 7,
  letterSpacing: 0.6,
  color: PALETTE.textLo,
  textTransform: "uppercase",
};

const titleStyle: CSSProperties = {
  fontSize: 18,
  color: PALETTE.textHi,
  fontWeight: 600,
  overflowWrap: "anywhere",
};

const subtitleStyle: CSSProperties = {
  fontSize: 13,
  color: PALETTE.textMid,
  overflowWrap: "anywhere",
};

const badgeStyle: CSSProperties = {
  background: "transparent",
  border: `1px solid ${PALETTE.border}`,
  color: PALETTE.textMid,
  fontSize: 11,
  padding: "3px 8px",
  borderRadius: 999,
  letterSpacing: 0.4,
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

const rowStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "150px 1fr",
  alignItems: "baseline",
  gap: 12,
  paddingBlock: 6,
  borderBottom: `1px solid ${PALETTE.border}`,
};

const fieldLabelStyle: CSSProperties = {
  color: PALETTE.textLo,
  fontSize: 12,
  letterSpacing: 0.4,
  textTransform: "uppercase",
  fontWeight: 600,
};

const fieldValueStyle: CSSProperties = {
  color: PALETTE.textHi,
  fontSize: 13,
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
  readonly numeric?: boolean;
}

export function FieldRow(props: FieldRowProps): ReactElement {
  const { fieldKey, label, value, numeric } = props;
  return (
    <div style={rowStyle} data-testid={`field-${fieldKey}`}>
      <span style={fieldLabelStyle}>{label}</span>
      <span
        style={numeric ? numericValueStyle : fieldValueStyle}
        data-testid={`field-${fieldKey}-value`}
      >
        {value || " "}
      </span>
    </div>
  );
}

export const fieldGridStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
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
  paddingTop: 6,
};

const linkStyle: CSSProperties = {
  color: PALETTE.lime,
  fontSize: 13,
  textDecoration: "none",
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
      <span
        style={{ ...fieldValueStyle, color: PALETTE.textLo }}
        data-testid="surface-link-text"
      >
        {label || String(value ?? "")}
      </span>
    </div>
  );
}

const emptyStyle: CSSProperties = {
  fontSize: 13,
  color: PALETTE.textLo,
  paddingBlock: 6,
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
  gap: 14,
  alignItems: "baseline",
  padding: "9px 12px",
  borderBottom: `1px solid ${PALETTE.border}`,
};

// Mono caps at the micro rung. A raw payload key is an identifier; setting it
// in the same sans face as the value would claim it is prose we chose.
const genericLabelStyle: CSSProperties = {
  fontFamily: SURFACE_MONO_FONT,
  fontSize: 9.5,
  letterSpacing: "0.11em",
  textTransform: "uppercase",
  color: TEXT_SUBTLE,
  overflowWrap: "anywhere",
};

const genericValueStyle: CSSProperties = {
  fontSize: 13,
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
  fontSize: 12,
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
  padding: "9px 12px",
  fontSize: 12,
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

// ---- The no-spec view (PRD-02) -------------------------------------------

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
 * could not use it, so "no spec matched" is still true, but it still tells us
 * which tool this was.
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
  gap: 9,
  padding: "9px 12px",
  fontSize: 12,
  lineHeight: 1.55,
  color: PALETTE.textMid,
  background: BAND_BG,
  border: `1px solid ${PALETTE.border}`,
  borderRadius: 10,
};

/** `.sf-note svg` — 13px, one rung quieter than the copy it introduces. */
const noteIconStyle: CSSProperties = {
  flex: "none",
  marginTop: 2,
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
  fontSize: 11,
  color: PALETTE.textHi,
  overflowWrap: "anywhere",
};

/** The design's `.sf-bar` — the card's closing band. */
const readOnlyBarStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 9,
  flexWrap: "wrap",
  padding: "9px 12px",
  background: BAND_BG,
  border: `1px solid ${PALETTE.border}`,
  borderRadius: 10,
};

const readOnlyCopyStyle: CSSProperties = {
  fontSize: 12,
  color: PALETTE.textLo,
  minWidth: 0,
};

/** `.sf-bar .c` drops its second clause to `--mut2`: the claim is "read-only",
 * the reason is a quieter aside. */
const readOnlyReasonStyle: CSSProperties = {
  color: TEXT_SUBTLE,
};

/**
 * The tier-3 generic view: the honest note, the payload as the tool sent it,
 * and the read-only footer.
 *
 * This is the degradation target for EVERY archetype — `TableRenderer`,
 * `RecordRenderer`, `BoardRenderer`, `DocRenderer` and `MessageRenderer` all
 * land here when `specFromState` returns `undefined` — so it is total over
 * `unknown`: `data` goes to {@link GenericFieldList}, which summarises rather
 * than recurses, and `tool` is gated by {@link displayToolName}.
 *
 * It is a REAL VIEW, not an error and not a loading state (design rule 04:
 * "Unknown degrades. Nothing in this pane ever renders as an error."). The note
 * says what happened and what happens next; the footer says what cannot happen
 * here at all.
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
          No spec matched{" "}
          {tool === undefined ? (
            // Never "undefined" on screen, and never an empty gap where a name
            // should be: with no name to give, the sentence names the THING
            // instead and stays true.
            "this tool result"
          ) : (
            // Inert text in a code register — never an anchor. `url_path` on a
            // spec is the one sanctioned link on a surface, and this tier has
            // no spec by definition (PRD-02 requirement 5 + non-goals).
            <code style={noteCodeStyle} data-testid="surface-no-spec-tool">
              {tool}
            </code>
          )}
          , so this is the payload as the tool sent it — top-level fields only,
          nested objects summarised. A spec will be generated and cached on the
          next call.
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
  gap: 6,
  paddingBlock: 8,
  paddingInline: 10,
  borderRadius: 8,
  background: PALETTE.limeBgSoft,
  border: `1px solid ${PALETTE.lime}`,
  marginBlock: 2,
};

const changeHeaderStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  flexWrap: "wrap",
};

const provenancePillStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  padding: "2px 8px",
  borderRadius: 999,
  border: `1px solid ${PALETTE.border}`,
  fontSize: 11,
  letterSpacing: 0.4,
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
  gap: 8,
  flexWrap: "wrap",
  fontSize: 13,
};

const previousValueStyle: CSSProperties = {
  color: PALETTE.textLo,
  textDecoration: "line-through",
  textDecorationColor: PALETTE.textLo,
  overflowWrap: "anywhere",
};

const arrowStyle: CSSProperties = {
  color: PALETTE.textMid,
  fontSize: 12,
};

const nextValueStyle: CSSProperties = {
  color: PALETTE.textHi,
  background: "color-mix(in srgb, var(--color-accent) 18%, transparent)",
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
