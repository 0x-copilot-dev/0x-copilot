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
  padding: "10px 12px",
  textAlign: "left",
  background: "transparent",
  color: "var(--color-text)",
};

// `.lrow__ic` — 28×28 leading icon box. Colour defaults to muted; a caller can
// override (e.g. a success-tinted wrapper for a live brand mark).
const iconSlotStyle: CSSProperties = {
  flex: "0 0 auto",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 28,
  height: 28,
  borderRadius: "var(--radius-md)",
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
  fontWeight: "var(--font-weight-semibold)",
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

export function Row({
  icon,
  title,
  chip,
  sub,
  meta,
  onActivate,
  ariaLabel,
  style,
  ...rest
}: RowProps): ReactElement {
  const interactive = onActivate !== undefined;

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
      role={interactive ? "button" : undefined}
      tabIndex={interactive ? 0 : undefined}
      aria-label={interactive ? ariaLabel : undefined}
      onClick={interactive ? onActivate : undefined}
      onKeyDown={interactive ? onKeyDown : undefined}
      style={{
        ...rowStyle,
        cursor: interactive ? "pointer" : undefined,
        ...style,
      }}
      {...rest}
    >
      {icon !== undefined ? (
        <span style={iconSlotStyle} aria-hidden="true" data-testid="row-icon">
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
    </div>
  );
}
