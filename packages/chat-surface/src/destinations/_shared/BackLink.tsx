// <BackLink> — the design `.backlink` quiet return control (PRD-10 D5).
//
// Owned by the package so BOTH hosts get it: it used to live in the web host
// (`ProjectsRoute.tsx:686-701`) as a 13px semibold accent-blue sans button, which
// meant desktop — once it got a detail view — would have to hand-roll a second
// one. Now the detail view renders this, and both hosts inherit the design.
//
// Design (`.backlink`, copilot.css:1721-1739): inline-flex, gap 6px,
// `var(--font-mono)`, 11px, `var(--mut)` (= `--color-text-muted`), transparent,
// border 0, padding 0, margin-bottom 14px, a leading 13×13 chevron svg, hover
// → `--color-text`.
//
// 11px has no exact rung; the nearest is `--font-size-2xs` (0.7rem = 11.2px, 0.2px
// off, below the LOW threshold) — use it, do not mint a rung (PRD-10 D5). Hover is
// carried by the `.ui-backlink` class (an inline style object cannot express
// `:hover`); the resting colour is inline so a host without the recipe still reads
// muted.
//
// Substrate-agnostic; token-driven only.

import type { CSSProperties, ReactElement } from "react";

export interface BackLinkProps {
  /** Fired on click / Enter / Space. */
  readonly onBack: () => void;
  /** Link label. Defaults to "All projects". */
  readonly label?: string;
  /** Optional test id override. */
  readonly testId?: string;
}

const style: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-2xs)",
  color: "var(--color-text-muted)",
  background: "transparent",
  border: 0,
  padding: 0,
  marginBottom: 14,
  cursor: "pointer",
};

export function BackLink({
  onBack,
  label = "All projects",
  testId = "back-link",
}: BackLinkProps): ReactElement {
  return (
    <button
      type="button"
      className="ui-backlink"
      style={style}
      onClick={onBack}
      data-testid={testId}
    >
      <svg
        width={13}
        height={13}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
        data-testid="back-link-chevron"
      >
        <polyline points="15 18 9 12 15 6" />
      </svg>
      {label}
    </button>
  );
}
