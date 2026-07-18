// <SkillCard> — one card in the Skills catalog grid (PR-4.9).
//
// Source: DESIGN-SPEC §3 (Skills: card grid — name, sub, N runs; Run /
// Edit) + PRD phase-4 FR-4.26 / FR-4.27. Renders one `SkillSummary` as a
// card: name (12.5px), the skill's short description as the sub-line, a
// mono `N runs` badge + mono relative `updated_at`, and two actions —
// **Run** (primary accent) and **Edit** (ghost).
//
// Pure presentation: no fetch, no router, no SSE. Run / Edit fire the
// host callbacks (`onRun` / `onEdit`); the host (PR-4.10 binder) starts
// the run + navigates to Run, or opens the skill editor route. Tokens
// only — every colour is a `var(--color-…)` so Appearance theme/accent
// swaps flow through.

import type { CSSProperties, ReactElement } from "react";

import type { SkillId, SkillSummary } from "@0x-copilot/api-types";

import { formatRelativeTime } from "../../util/time";

// ===========================================================================
// `N runs` badge label (exported — reused by the destination + tests)
// ===========================================================================

/**
 * Format the run-count badge. Guards against non-finite / negative wire
 * values (defaults to 0) and singularises "1 run".
 */
export function runCountLabel(count: number): string {
  const safe = Number.isFinite(count) && count > 0 ? Math.floor(count) : 0;
  return `${safe} ${safe === 1 ? "run" : "runs"}`;
}

// ===========================================================================
// Props
// ===========================================================================

export interface SkillCardProps {
  readonly skill: SkillSummary;
  /** "Run" — host starts a run of this skill + navigates to Run. */
  readonly onRun?: (id: SkillId) => void;
  /** "Edit" — host opens the skill editor route. */
  readonly onEdit?: (id: SkillId) => void;
  /** Reference instant — test seam for relative-time formatting. */
  readonly now: number;
}

// ===========================================================================
// Component
// ===========================================================================

export function SkillCard({
  skill,
  onRun,
  onEdit,
  now,
}: SkillCardProps): ReactElement {
  const cardStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 10,
    padding: 14,
    borderRadius: "var(--radius-md, 12px)",
    border: "1px solid var(--color-border, #232325)",
    backgroundColor: "var(--color-surface, #1a1a1c)",
    color: "var(--color-text, #ededee)",
    boxSizing: "border-box",
    minWidth: 0,
  };
  const headStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    minWidth: 0,
  };
  const nameStyle: CSSProperties = {
    flex: 1,
    minWidth: 0,
    fontSize: "12.5px",
    fontWeight: 600,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const runsBadgeStyle: CSSProperties = {
    flexShrink: 0,
    fontFamily: "var(--font-mono, ui-monospace, monospace)",
    fontSize: "var(--font-size-2xs, 11px)",
    color: "var(--color-text-muted, #b4b4b8)",
    whiteSpace: "nowrap",
  };
  const descStyle: CSSProperties = {
    fontSize: "var(--font-size-xs, 12px)",
    color: "var(--color-text-muted, #b4b4b8)",
    lineHeight: 1.45,
    // Two-line clamp — description is prose, not a mono sub.
    display: "-webkit-box",
    WebkitBoxOrient: "vertical",
    WebkitLineClamp: 2,
    overflow: "hidden",
    minHeight: "2.9em",
  };
  const footStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    marginTop: "auto",
  };
  const timeStyle: CSSProperties = {
    fontFamily: "var(--font-mono, ui-monospace, monospace)",
    fontSize: "var(--font-size-2xs, 11px)",
    color: "var(--color-text-subtle, #7e7e84)",
    whiteSpace: "nowrap",
  };
  const actionRowStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 6,
    marginLeft: "auto",
    flexShrink: 0,
  };
  const runButtonStyle: CSSProperties = {
    height: 28,
    padding: "0 12px",
    borderRadius: "var(--radius-sm, 6px)",
    border: "1px solid var(--color-accent, #d97757)",
    backgroundColor: "var(--color-accent, #d97757)",
    color: "var(--color-accent-contrast, #1a0f0a)",
    fontSize: "var(--font-size-xs, 12px)",
    fontWeight: 600,
    cursor: "pointer",
  };
  const editButtonStyle: CSSProperties = {
    height: 28,
    padding: "0 12px",
    borderRadius: "var(--radius-sm, 6px)",
    border: "1px solid var(--color-border-strong, #2a2a2c)",
    backgroundColor: "transparent",
    color: "var(--color-text, #ededee)",
    fontSize: "var(--font-size-xs, 12px)",
    fontWeight: 600,
    cursor: "pointer",
  };

  return (
    <article
      style={cardStyle}
      data-testid="skill-card"
      data-skill-id={skill.id}
    >
      <div style={headStyle}>
        <span
          style={nameStyle}
          data-testid="skill-card-name"
          title={skill.name}
        >
          {skill.name}
        </span>
        <span style={runsBadgeStyle} data-testid="skill-card-runs">
          {runCountLabel(skill.run_count)}
        </span>
      </div>

      {skill.description.length > 0 ? (
        <div style={descStyle} data-testid="skill-card-description">
          {skill.description}
        </div>
      ) : null}

      <div style={footStyle}>
        <span style={timeStyle} data-testid="skill-card-time">
          {formatRelativeTime(skill.updated_at, now)}
        </span>
        <div style={actionRowStyle}>
          {onRun !== undefined ? (
            <button
              type="button"
              data-testid="skill-card-run"
              onClick={() => onRun(skill.id)}
              style={runButtonStyle}
              aria-label={`Run ${skill.name}`}
            >
              Run
            </button>
          ) : null}
          {onEdit !== undefined ? (
            <button
              type="button"
              data-testid="skill-card-edit"
              onClick={() => onEdit(skill.id)}
              style={editButtonStyle}
              aria-label={`Edit ${skill.name}`}
            >
              Edit
            </button>
          ) : null}
        </div>
      </div>
    </article>
  );
}
