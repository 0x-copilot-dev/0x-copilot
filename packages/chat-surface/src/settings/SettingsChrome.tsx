// Settings shell chrome primitives (DESIGN-SPEC §4).
//
//   <SetCard>          `.set-card` — head (h3 + meta) + body
//   <SecHead>          mono uppercase group / sub-section heading
//   <SetNote>          `.set-note` — inset note with optional icon (info/warn/danger)
//   <Frow>             `.frow` — label + hint on the left, control on the right
//   <Krow>             `.krow` — logo + name + sub + trailing actions
//   <SettingsNavItem>  216px-nav item (icon + label + optional mono tag;
//                      active = --panel2 bg + 2px accent left bar)
//
// These are the reusable *chrome*; the actual sections (Profile, Appearance,
// Provider keys, …) are built on top of them in PR-5.3…PR-5.9. The nav
// container / tablist and content router are PR-5.1's `SettingsSurface`.
//
// Substrate-agnostic (no browser globals). Colors resolve ONLY to
// design-system v2 tokens (var(--color-*)); no hard-coded hex. Where the design
// system already ships an atom (Button / Toggle / Select / TextInput / Badge),
// callers pass it in as the control — these primitives lay out chrome, they do
// not re-implement form controls.

import {
  type ButtonHTMLAttributes,
  type CSSProperties,
  type HTMLAttributes,
  type ReactElement,
  type ReactNode,
} from "react";

// ---------------------------------------------------------------------------
// SecHead — mono uppercase heading (nav group heading / content sub-section).
// Non-interactive (DESIGN-SPEC §4: "group headings non-clickable").
// ---------------------------------------------------------------------------

export interface SecHeadProps extends HTMLAttributes<HTMLDivElement> {
  readonly children: ReactNode;
}

const secHeadStyle: CSSProperties = {
  fontFamily: "var(--font-mono)",
  // Design .set-sec__head mono group header = 9px / .14em tracking.
  fontSize: "var(--font-size-3xs)",
  fontWeight: "var(--font-weight-semibold)",
  letterSpacing: "0.14em",
  textTransform: "uppercase",
  color: "var(--color-text-subtle)",
};

export function SecHead({
  children,
  style,
  ...rest
}: SecHeadProps): ReactElement {
  return (
    <div style={{ ...secHeadStyle, ...style }} {...rest}>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SecTitle — the section heading level (design `.set-sec__head`). A 17px/600
// h1 with an optional muted description below it, sitting ABOVE the cards.
// This is the top of the type hierarchy the card h3 (12.5px) used to swallow —
// restoring it gives each settings section a prominent title again.
// ---------------------------------------------------------------------------

export interface SecTitleProps extends Omit<
  HTMLAttributes<HTMLElement>,
  "title"
> {
  readonly title: ReactNode;
  /** Muted description under the heading (design 12px / line-height 1.6). */
  readonly description?: ReactNode;
}

const secTitleHeadingStyle: CSSProperties = {
  margin: 0,
  fontFamily: "var(--font-display)",
  // Design .set-sec__head h1 = 17px; --font-size-xl (18px) is the closest token.
  fontSize: "var(--font-size-xl)",
  fontWeight: "var(--font-weight-semibold)",
  letterSpacing: "-0.01em",
  color: "var(--color-text)",
};

const secTitleDescStyle: CSSProperties = {
  margin: "4px 0 0",
  fontSize: "var(--font-size-xs)",
  lineHeight: 1.6,
  color: "var(--color-text-muted)",
};

export function SecTitle({
  title,
  description,
  style,
  ...rest
}: SecTitleProps): ReactElement {
  return (
    <div style={style} {...rest}>
      <h1 style={secTitleHeadingStyle}>{title}</h1>
      {description !== undefined ? (
        <p style={secTitleDescStyle}>{description}</p>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SetCard — `.set-card`. A titled content card. Head shows an h3 title plus an
// optional `meta` line and an optional right-aligned `actions` slot.
// ---------------------------------------------------------------------------

export interface SetCardProps extends Omit<
  HTMLAttributes<HTMLElement>,
  "title"
> {
  /** Card heading (rendered as an h3) — a node, not the DOM `title` attribute. */
  readonly title?: ReactNode;
  /** Sub-line under the title (DESIGN-SPEC §4 "head h3 + meta"). */
  readonly meta?: ReactNode;
  /** Right-aligned head slot (e.g. a small action button). */
  readonly actions?: ReactNode;
  readonly children?: ReactNode;
}

const setCardStyle: CSSProperties = {
  backgroundColor: "var(--color-surface)",
  border: "1px solid var(--color-border)",
  // Design --r 8px (was --radius-lg 12px).
  borderRadius: "var(--radius-md)",
  padding: "var(--space-lg)",
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-md)",
};

const setCardHeadStyle: CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  justifyContent: "space-between",
  gap: "var(--space-md)",
};

const setCardTitleStyle: CSSProperties = {
  margin: 0,
  fontFamily: "var(--font-display)",
  // Design .set-card head h3 = 12.5px (was --font-size-md 14px). The 17px
  // section heading now lives above the cards via <SecTitle> (design hierarchy).
  fontSize: "var(--font-size-xs)",
  fontWeight: "var(--font-weight-semibold)",
  letterSpacing: "-0.01em",
  color: "var(--color-text)",
};

const setCardMetaStyle: CSSProperties = {
  margin: "3px 0 0",
  fontSize: "var(--font-size-xs)",
  lineHeight: "var(--line-height-base)",
  color: "var(--color-text-muted)",
};

export function SetCard({
  title,
  meta,
  actions,
  children,
  style,
  ...rest
}: SetCardProps): ReactElement {
  const hasHead =
    title !== undefined || meta !== undefined || actions !== undefined;
  return (
    <section style={{ ...setCardStyle, ...style }} {...rest}>
      {hasHead ? (
        <div style={setCardHeadStyle}>
          <div style={{ minWidth: 0 }}>
            {title !== undefined ? (
              <h3 style={setCardTitleStyle}>{title}</h3>
            ) : null}
            {meta !== undefined ? <p style={setCardMetaStyle}>{meta}</p> : null}
          </div>
          {actions !== undefined ? <div>{actions}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}

// ---------------------------------------------------------------------------
// SetNote — `.set-note`. Inset note with an optional leading icon. `info` is
// the quiet default; `warning`/`danger` map to the semantic amber/ember
// tokens (single-accent discipline, DESIGN-SPEC §0). Not an error alert — use
// role="alert" errors for facade-unreachable states.
// ---------------------------------------------------------------------------

export type SetNoteTone = "info" | "warning" | "danger";

export interface SetNoteProps extends HTMLAttributes<HTMLDivElement> {
  readonly icon?: ReactNode;
  readonly tone?: SetNoteTone;
  readonly children: ReactNode;
}

function setNoteColors(tone: SetNoteTone): {
  bg: string;
  border: string;
  text: string;
} {
  switch (tone) {
    case "warning":
      return {
        bg: "var(--color-warning-bg)",
        border: "var(--color-warning)",
        text: "var(--color-text)",
      };
    case "danger":
      return {
        bg: "var(--color-danger-bg)",
        border: "var(--color-danger)",
        text: "var(--color-text)",
      };
    default:
      return {
        // Design --ink2 dark inset (not the accent-tinted soft fill).
        bg: "var(--color-bg-elevated)",
        border: "var(--color-border)",
        text: "var(--color-text-muted)",
      };
  }
}

// Design `.set-note strong` — emphasized runs brighten to --tx (text-strong).
// Scoped to the note via a data attribute (inline styles can't reach a
// descendant <strong>; a scoped <style> is the established chat-surface idiom —
// see AppRail / TcInlineDiff).
const SET_NOTE_STRONG_CSS =
  "[data-set-note] strong{color:var(--color-text-strong);font-weight:var(--font-weight-semibold);}";

export function SetNote({
  icon,
  tone = "info",
  children,
  style,
  ...rest
}: SetNoteProps): ReactElement {
  const c = setNoteColors(tone);
  return (
    <div
      data-tone={tone}
      data-set-note=""
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: "var(--space-sm)",
        padding: "10px 12px",
        borderRadius: "var(--radius-md)",
        backgroundColor: c.bg,
        border: `1px solid ${c.border}`,
        color: c.text,
        fontSize: "var(--font-size-2xs)",
        lineHeight: "var(--line-height-base)",
        ...style,
      }}
      {...rest}
    >
      <style>{SET_NOTE_STRONG_CSS}</style>
      {icon !== undefined ? (
        <span aria-hidden="true" style={{ flex: "0 0 auto", lineHeight: 1 }}>
          {icon}
        </span>
      ) : null}
      <div style={{ minWidth: 0 }}>{children}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Frow — `.frow`. A settings field row: label (+ optional hint) on the left,
// control on the right. When `htmlFor` is given the label associates with the
// caller's control; otherwise the label is a plain span (the control labels
// itself).
// ---------------------------------------------------------------------------

export interface FrowProps {
  readonly label: ReactNode;
  readonly hint?: ReactNode;
  /** Associates the label with a control that carries this id. */
  readonly htmlFor?: string;
  /** The control (design-system Select / Toggle / TextInput / SegmentedControl…). */
  readonly children: ReactNode;
  readonly className?: string;
  readonly style?: CSSProperties;
}

const frowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: "var(--space-lg)",
  // Design .frow — 10px/14px padding, top hairline (rows divide from the top).
  padding: "10px 14px",
  borderTop: "1px solid var(--color-border)",
};

const frowLabelStyle: CSSProperties = {
  fontSize: "var(--font-size-xs)",
  fontWeight: "var(--font-weight-medium)",
  color: "var(--color-text)",
};

const frowHintStyle: CSSProperties = {
  margin: "2px 0 0",
  fontSize: "var(--font-size-2xs)",
  lineHeight: "var(--line-height-base)",
  color: "var(--color-text-subtle)",
};

export function Frow({
  label,
  hint,
  htmlFor,
  children,
  className,
  style,
}: FrowProps): ReactElement {
  const labelContent = (
    <>
      <span style={frowLabelStyle}>{label}</span>
      {hint !== undefined ? <p style={frowHintStyle}>{hint}</p> : null}
    </>
  );
  return (
    <div className={className} style={{ ...frowStyle, ...style }}>
      <div style={{ minWidth: 0 }}>
        {htmlFor !== undefined ? (
          <label htmlFor={htmlFor}>{labelContent}</label>
        ) : (
          labelContent
        )}
      </div>
      <div style={{ flex: "0 0 auto" }}>{children}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Krow — `.krow`. A list row for connected items (provider keys, local
// models, dev tokens): leading logo, name (+ optional sub), trailing actions.
// The logo is neutralized to a monochrome chip by default (single-accent
// discipline); callers pass their own glyph as `logo`.
// ---------------------------------------------------------------------------

export interface KrowProps extends HTMLAttributes<HTMLDivElement> {
  readonly logo?: ReactNode;
  readonly name: ReactNode;
  readonly sub?: ReactNode;
  readonly actions?: ReactNode;
}

// Design rows are FLAT — a top hairline divides them, no fill or radius (they
// are not discrete cards). The list container drops its inter-row gap so rows
// sit flush and the borders read as continuous dividers.
const krowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-md)",
  padding: "11px 14px",
  borderTop: "1px solid var(--color-border)",
};

const krowLogoStyle: CSSProperties = {
  flex: "0 0 auto",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 30,
  height: 30,
  borderRadius: "var(--radius-md)",
  backgroundColor: "var(--color-surface-elevated)",
  color: "var(--color-text-strong)",
  overflow: "hidden",
};

const krowNameStyle: CSSProperties = {
  fontSize: "var(--font-size-xs)",
  fontWeight: "var(--font-weight-medium)",
  color: "var(--color-text)",
};

const krowSubStyle: CSSProperties = {
  margin: "1px 0 0",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-2xs)",
  color: "var(--color-text-subtle)",
};

export function Krow({
  logo,
  name,
  sub,
  actions,
  style,
  ...rest
}: KrowProps): ReactElement {
  return (
    <div style={{ ...krowStyle, ...style }} {...rest}>
      {logo !== undefined ? (
        <span style={krowLogoStyle} aria-hidden="true">
          {logo}
        </span>
      ) : null}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={krowNameStyle}>{name}</div>
        {sub !== undefined ? <div style={krowSubStyle}>{sub}</div> : null}
      </div>
      {actions !== undefined ? (
        <div
          style={{
            flex: "0 0 auto",
            display: "inline-flex",
            alignItems: "center",
            gap: "var(--space-sm)",
          }}
        >
          {actions}
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SettingsNavItem — a single 216px-nav row. Icon + label + optional mono tag
// (e.g. "BYOK"). Active mirrors the rail-active spec (DESIGN-SPEC §1/§4):
// --panel2 bg + a 2px accent bar on the left edge. Rendered as a real
// <button>; the container (PR-5.1 SettingsSurface) owns tablist/roving-focus
// semantics, so `aria-current="page"` marks the active item here.
// ---------------------------------------------------------------------------

export interface SettingsNavItemProps extends Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  "children"
> {
  readonly icon?: ReactNode;
  readonly label: ReactNode;
  /** Optional mono tag (DESIGN-SPEC §4 Provider keys "BYOK"). */
  readonly tag?: ReactNode;
  readonly active?: boolean;
}

export function SettingsNavItem({
  icon,
  label,
  tag,
  active = false,
  style,
  ...rest
}: SettingsNavItemProps): ReactElement {
  return (
    <button
      type="button"
      data-active={active ? "true" : undefined}
      aria-current={active ? "page" : undefined}
      style={{
        position: "relative",
        display: "flex",
        alignItems: "center",
        gap: "var(--space-sm)",
        width: "100%",
        // Design .set-nav__item — 6px/8px pad, 6px radius (was 7px/10px, --radius-md).
        padding: "6px 8px",
        borderRadius: "var(--radius-sm)",
        border: "1px solid transparent",
        backgroundColor: active ? "var(--color-surface-muted)" : "transparent",
        color: active ? "var(--color-text)" : "var(--color-text-muted)",
        font: "inherit",
        // Design .set-nav__item is 12px (nearest token --font-size-xs 12.5px);
        // the settings-item spec has NO rail-style accent left bar — the active
        // affordance is bg + the accent-coloured icon (PRD-E).
        fontSize: "var(--font-size-xs)",
        fontWeight: active
          ? "var(--font-weight-medium)"
          : "var(--font-weight-regular)",
        textAlign: "left",
        cursor: "pointer",
        transition:
          "background-color var(--duration-fast) var(--ease-standard)",
        ...style,
      }}
      {...rest}
    >
      {icon !== undefined ? (
        <span
          aria-hidden="true"
          data-settings-nav-icon=""
          style={{
            flex: "0 0 auto",
            display: "inline-flex",
            lineHeight: 1,
            // Active item tints its icon with the accent (design .set-nav__item
            // [data-active] svg{color:var(--accent)}).
            color: active ? "var(--color-accent)" : undefined,
          }}
        >
          {icon}
        </span>
      ) : null}
      <span style={{ flex: 1, minWidth: 0 }}>{label}</span>
      {tag !== undefined ? (
        <span
          style={{
            flex: "0 0 auto",
            fontFamily: "var(--font-mono)",
            fontSize: "var(--font-size-2xs)",
            letterSpacing: "0.04em",
            color: "var(--color-text-subtle)",
            border: "1px solid var(--color-border)",
            borderRadius: "var(--radius-sm)",
            padding: "1px 5px",
          }}
        >
          {tag}
        </span>
      ) : null}
    </button>
  );
}
