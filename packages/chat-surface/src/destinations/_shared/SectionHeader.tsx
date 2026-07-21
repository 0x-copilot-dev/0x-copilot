// <SectionHeader> — the design `.sect-h` mono section header.
//
// Source: docs/plan/frontend-parity-v3/PRD-G-destination-parity.md (FR-G.1).
// Mono, ~9.5px (--font-size-2xs), uppercase, letter-spacing .12em, subtle
// colour. Renders an <h2> so a section can associate it via `aria-labelledby`
// (pass `headingId`). An optional `count` chip sits inline after the label; an
// optional `action` node is pushed to the far right (the Chats "＋ New chat"
// primary lives here, not a big top-right button).
//
// Substrate-agnostic; token-driven only.

import type {
  CSSProperties,
  HTMLAttributes,
  ReactElement,
  ReactNode,
} from "react";

export interface SectionHeaderProps extends HTMLAttributes<HTMLDivElement> {
  /** The heading text (rendered inside the <h2>). */
  readonly children: ReactNode;
  /** Id for the <h2>, so a section can `aria-labelledby` it. */
  readonly headingId?: string;
  /** Inline chip after the label (e.g. a count pill). */
  readonly count?: ReactNode;
  /** Right-aligned action (e.g. a small primary "New chat" button). */
  readonly action?: ReactNode;
}

const wrapStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-sm)",
};

// `.sect-h` — mono uppercase, tiny, wide tracking, subtle colour.
const headingStyle: CSSProperties = {
  margin: 0,
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-2xs)",
  fontWeight: "var(--font-weight-semibold)",
  letterSpacing: "0.12em",
  textTransform: "uppercase",
  color: "var(--color-text-subtle)",
};

const actionSlotStyle: CSSProperties = {
  marginInlineStart: "auto",
  display: "inline-flex",
  alignItems: "center",
};

export function SectionHeader({
  children,
  headingId,
  count,
  action,
  className,
  style,
  ...rest
}: SectionHeaderProps): ReactElement {
  return (
    <div
      className={className === undefined ? "sect-h" : `sect-h ${className}`}
      style={{ ...wrapStyle, ...style }}
      data-testid="section-header"
      {...rest}
    >
      <h2
        id={headingId}
        style={headingStyle}
        data-testid="section-header-label"
      >
        {children}
      </h2>
      {count !== undefined ? (
        <span
          style={{ display: "inline-flex", alignItems: "center" }}
          data-testid="section-header-count"
        >
          {count}
        </span>
      ) : null}
      {action !== undefined ? (
        <div style={actionSlotStyle} data-testid="section-header-action">
          {action}
        </div>
      ) : null}
    </div>
  );
}
