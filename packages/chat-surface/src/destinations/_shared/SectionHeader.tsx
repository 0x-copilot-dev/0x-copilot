// <SectionHeader> — the design `.sect-h` mono section header.
//
// Source: docs/plan/frontend-parity-v3/PRD-G-destination-parity.md (FR-G.1).
//
// TYPE comes from the design-system recipe `.ui-mono-caps` on the <h2> label:
// mono, 9.5px (--font-size-mono-9-5), regular weight, uppercase,
// --tracking-mono-caps. It is applied to the LABEL and never to the wrapper, because the
// wrapper also carries the count pill and the right-aligned action slot (the
// Chats "＋ New chat" primary lives there) — a type recipe on the wrapper would
// mono-uppercase that CTA.
//
// (Historical note, because the wrong comment caused the bug: this file used to
// name the 2xs SANS rung as if it were 9.5px. That rung is 11.2px —
// so the header shipped 18% too large, at semibold, with a raw 0.12em literal.)
//
// BLOCK RHYTHM comes from `.ui-section-head` on the wrapper: the design's
// `margin: 22px 0 10px` with `:first-child{margin-top:0}` (copilot.css:1569-1573),
// which cannot be expressed inline at all.
//
// Renders an <h2> so a section can associate it via `aria-labelledby` (pass
// `headingId`).
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

// Family / size / tracking / case all come from `.ui-mono-caps`. The only
// per-role overrides kept inline are the <h2> margin reset and the colour: the
// design's `.sect-h` is --mut2 (= --color-text-subtle), one rung quieter than
// the recipe's default --color-text-muted, which its other consumer (the login
// divider) wants. Overriding the colour here beats changing the shared recipe.
const headingStyle: CSSProperties = {
  margin: 0,
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
      // Block rhythm + flex row come from `.ui-section-head`. The mock's
      // vestigial `.sect-h` on the wrapper carried no CSS (and would have
      // mono-uppercased the count pill + action CTA); the type recipe lives on
      // the <h2> label below (`.ui-mono-caps`, C13). Deleted in PRD-13.
      className={
        className === undefined
          ? "ui-section-head"
          : `ui-section-head ${className}`
      }
      style={style}
      data-testid="section-header"
      {...rest}
    >
      <h2
        id={headingId}
        className="ui-mono-caps"
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
