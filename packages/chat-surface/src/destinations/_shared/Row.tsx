// <Row> — the design `.lrow` list row.
//
// Source: docs/plan/frontend-parity-v3/PRD-G-destination-parity.md (FR-G.1).
// One row of a list-surface `.rowlist` card:
//   * an optional leading icon slot (28×28, the design `.lrow__ic`) — a shared
//     <Icon> or the brand <BrandMark> for live rows;
//   * a main column — a 12.5px title (--color-text) with an optional status
//     `chip` inline after the name, plus an optional 11px body-font sub-line
//     (--color-text-subtle);
//   * an optional right meta column — mono time.
//
// Two modes:
//   * "as button" — pass `onActivate`; the row becomes a `role="button"`,
//     focusable control that fires on click and Enter/Space (rich content keeps
//     it a div, not a native <button>, so nested links compose and jsdom fires
//     the keyboard path). `ariaLabel` names it.
//   * inert — no `onActivate`; a plain container (the title may itself be an
//     <ItemLink> that owns navigation).
//
// The row is transparent — its `.rowlist` parent supplies the card border,
// radius, surface, and the hairline separators between rows. Substrate-agnostic;
// token-driven only.

import type {
  CSSProperties,
  HTMLAttributes,
  KeyboardEvent as ReactKeyboardEvent,
  ReactElement,
  ReactNode,
} from "react";

export interface RowProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  "title"
> {
  /** Leading 28×28 icon slot content (an <Icon> or the brand <BrandMark>). */
  readonly icon?: ReactNode;
  /** Main row title. May itself be an interactive node (e.g. an <ItemLink>). */
  readonly title: ReactNode;
  /** Optional status chip rendered inline, immediately after the title. */
  readonly chip?: ReactNode;
  /** Optional secondary line under the title (body font, subtle colour). */
  readonly sub?: ReactNode;
  /** Optional right-aligned meta column (mono time). */
  readonly meta?: ReactNode;
  /**
   * Optional trailing-slot content, rendered after `meta` in a wrapper that
   * ALWAYS reserves 16px (the design's `<span style={{width:16}}/>` on
   * non-navigable rows). A navigable row fills it with a chevron; an inert one
   * leaves it empty. Reserving it unconditionally keeps the `meta` column from
   * ragging on the rows that have no trailing glyph (PRD-08 D4). It goes on the
   * shared primitive because Chats, Tools and Skills share the same "which of
   * these can I click" problem.
   */
  readonly trailing?: ReactNode;
  /**
   * Tone of the 28x28 icon TILE (applied to the slot's `color`, so it reaches
   * the tile and the glyph inside it). `"success"` tints a live row jade;
   * default is muted. Distinct from wrapping the glyph in a coloured span — the
   * wrapper never reaches the tile (PRD-08 D5).
   */
  readonly iconTone?: "default" | "success";
  /**
   * When provided, the row is an activatable control (click + Enter/Space).
   * When omitted, the row is inert chrome.
   */
  readonly onActivate?: () => void;
  /** Accessible name for the activatable-row control. */
  readonly ariaLabel?: string;
}

const rowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-md)",
  width: "100%",
  minWidth: 0,
  boxSizing: "border-box",
  // `.lrow { padding: 11px 14px }` (copilot.css:1586). PRD-08 D9 — one recipe
  // for every list destination; `gap` already matches at 12px.
  padding: "11px 14px",
  textAlign: "left",
  background: "transparent",
  color: "var(--color-text)",
};

// `.lrow__ic` — 28×28 leading icon TILE (copilot.css:1617-1626): a `--panel3`
// surface (== `--color-surface-elevated`; NOT `--color-surface-muted`, which is
// the hover colour from D6 and would make the tile vanish on hover), 7px radius
// (a one-off literal — the design is 7px and `--radius-md` is 8px; a
// `--radius-tile` token belongs to PRD-01 if it wants one), grid-centred. The
// slot's `color` carries the tone (default muted, or jade for a live row via
// `iconTone`), so it reaches BOTH the tile and the glyph — the old inner
// coloured `<span>` never reached the tile (PRD-08 D5).
const iconSlotStyle: CSSProperties = {
  flex: "0 0 auto",
  display: "grid",
  placeItems: "center",
  width: 28,
  height: 28,
  borderRadius: 7,
  background: "var(--color-surface-elevated)",
  color: "var(--color-text-muted)",
};

const mainColStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
  flex: 1,
  minWidth: 0,
};

const titleRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-sm)",
  minWidth: 0,
};

const titleStyle: CSSProperties = {
  fontSize: "var(--font-size-xs)",
  // `.lrow__name { font-weight: 500 }` (copilot.css:1637) — one recipe for
  // Activity, Chats, Projects, Library, Tools (PRD-08 D9, absorbed from PRD-04).
  fontWeight: "var(--font-weight-medium)",
  color: "var(--color-text)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  minWidth: 0,
};

const subStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs)",
  color: "var(--color-text-subtle)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const metaStyle: CSSProperties = {
  flex: "0 0 auto",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-2xs)",
  color: "var(--color-text-subtle)",
  whiteSpace: "nowrap",
};

// The trailing slot — always 16px wide, reserved even when empty (the design's
// `<span style={{width:16}}/>`). No `color` on the wrapper: the empty spacer
// inherits the row's colour exactly as the design's empty span does; the chevron
// glyph is tinted `--color-text-subtle` by the `.ui-list-row` recipe (the design
// puts the mut2 colour on `.lrow > svg`, not on the spacer), PRD-08 D4.
const trailingSlotStyle: CSSProperties = {
  flex: "0 0 auto",
  width: 16,
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "flex-end",
};

export function Row({
  icon,
  title,
  chip,
  sub,
  meta,
  trailing,
  iconTone = "default",
  onActivate,
  ariaLabel,
  style,
  className,
  ...rest
}: RowProps): ReactElement {
  const interactive = onActivate !== undefined;

  // `.ui-list-row` (design-system) owns the `:hover`/`:focus-visible` background
  // and forces the tile glyph to 15px — both things an inline style object
  // cannot express (PRD-08 D6). Merge with any caller className; cursor lives in
  // the recipe (`.ui-list-row[role="button"]{cursor:pointer}`), not inline.
  const rowClassName =
    className !== undefined && className !== ""
      ? `ui-list-row ${className}`
      : "ui-list-row";

  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (
      onActivate !== undefined &&
      (event.key === "Enter" || event.key === " " || event.key === "Spacebar")
    ) {
      // Space would scroll the page; Enter is a no-op default on a div.
      event.preventDefault();
      onActivate();
    }
  };

  return (
    <div
      data-testid="row"
      className={rowClassName}
      role={interactive ? "button" : undefined}
      tabIndex={interactive ? 0 : undefined}
      aria-label={interactive ? ariaLabel : undefined}
      onClick={interactive ? onActivate : undefined}
      onKeyDown={interactive ? onKeyDown : undefined}
      style={{
        ...rowStyle,
        ...style,
      }}
      {...rest}
    >
      {icon !== undefined ? (
        <span
          style={{
            ...iconSlotStyle,
            color:
              iconTone === "success"
                ? "var(--color-success)"
                : iconSlotStyle.color,
          }}
          aria-hidden="true"
          data-testid="row-icon"
        >
          {icon}
        </span>
      ) : null}
      <span style={mainColStyle}>
        <span style={titleRowStyle}>
          <span style={titleStyle} data-testid="row-title">
            {title}
          </span>
          {chip !== undefined ? (
            <span
              style={{ flex: "0 0 auto", display: "inline-flex" }}
              data-testid="row-chip"
            >
              {chip}
            </span>
          ) : null}
        </span>
        {sub !== undefined && sub !== null && sub !== "" ? (
          <span style={subStyle} data-testid="row-sub">
            {sub}
          </span>
        ) : null}
      </span>
      {meta !== undefined ? (
        <span style={metaStyle} data-testid="row-meta">
          {meta}
        </span>
      ) : null}
      <span style={trailingSlotStyle} data-testid="row-trailing">
        {trailing}
      </span>
    </div>
  );
}
