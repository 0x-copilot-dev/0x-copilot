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
  fontSize: "var(--font-size-2xs)",
  fontWeight: "var(--font-weight-semibold)",
  letterSpacing: "0.06em",
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
  borderRadius: "var(--radius-lg)",
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
  fontSize: "var(--font-size-md)",
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
        bg: "var(--color-accent-soft)",
        border: "var(--color-border)",
        text: "var(--color-text-muted)",
      };
  }
}

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
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: "var(--space-sm)",
        padding: "10px 12px",
        borderRadius: "var(--radius-md)",
        backgroundColor: c.bg,
        border: `1px solid ${c.border}`,
        color: c.text,
        fontSize: "var(--font-size-xs)",
        lineHeight: "var(--line-height-base)",
        ...style,
      }}
      {...rest}
    >
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
  padding: "var(--space-sm) 0",
  borderBottom: "1px solid var(--color-border)",
};

const frowLabelStyle: CSSProperties = {
  fontSize: "var(--font-size-sm)",
  fontWeight: "var(--font-weight-medium)",
  color: "var(--color-text)",
};

const frowHintStyle: CSSProperties = {
  margin: "2px 0 0",
  fontSize: "var(--font-size-xs)",
  lineHeight: "var(--line-height-base)",
  color: "var(--color-text-muted)",
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

const krowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-md)",
  padding: "var(--space-sm) var(--space-md)",
  borderRadius: "var(--radius-md)",
  border: "1px solid var(--color-border)",
  backgroundColor: "var(--color-surface-muted)",
};

const krowLogoStyle: CSSProperties = {
  flex: "0 0 auto",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 30,
  height: 30,
  borderRadius: "var(--radius-md)",
  backgroundColor: "var(--color-surface)",
  color: "var(--color-text-muted)",
  overflow: "hidden",
};

const krowNameStyle: CSSProperties = {
  fontSize: "var(--font-size-sm)",
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
        padding: "7px 10px",
        borderRadius: "var(--radius-md)",
        border: "1px solid transparent",
        backgroundColor: active ? "var(--color-surface-muted)" : "transparent",
        color: active ? "var(--color-text)" : "var(--color-text-muted)",
        font: "inherit",
        fontSize: "var(--font-size-sm)",
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
      {active ? (
        <span
          aria-hidden="true"
          style={{
            position: "absolute",
            left: 0,
            top: 6,
            bottom: 6,
            width: 2,
            borderRadius: "var(--radius-full)",
            backgroundColor: "var(--color-accent)",
          }}
        />
      ) : null}
      {icon !== undefined ? (
        <span
          aria-hidden="true"
          style={{ flex: "0 0 auto", display: "inline-flex", lineHeight: 1 }}
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
