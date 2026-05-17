// <StatusPill status label /> — one component, one tone-to-token mapping.
//
// Source: cross-audit.md §1.6 + destinations-master-prd §4.2. Every
// destination's status-coloured chip flows through this primitive; no
// per-destination color choices.
//
// Tone mapping reads design-system tokens. The five tones cover every
// status across destinations (run state, connector health, approval
// state, etc). When a destination wants a sixth tone, extend the union
// here — never inline a new color.

import type { CSSProperties, ReactElement } from "react";

export type StatusTone = "ok" | "error" | "warning" | "info" | "muted";

export interface StatusPillProps {
  readonly status: StatusTone;
  readonly label: string;
  readonly className?: string;
}

interface TonePalette {
  readonly fg: string;
  readonly bg: string;
  readonly border: string;
}

const PALETTE: Readonly<Record<StatusTone, TonePalette>> = {
  ok: {
    fg: "var(--color-success, #6ec48c)",
    bg: "var(--color-success-bg, #1a2f23)",
    border: "var(--color-success, #6ec48c)",
  },
  error: {
    fg: "var(--color-danger, #d97777)",
    bg: "var(--color-danger-bg, #321a1a)",
    border: "var(--color-danger, #d97777)",
  },
  warning: {
    fg: "var(--color-warning, #d9a857)",
    bg: "var(--color-warning-bg, #322615)",
    border: "var(--color-warning, #d9a857)",
  },
  info: {
    fg: "var(--color-accent, #d97757)",
    bg: "var(--color-bg-accent-subtle, #2a1a14)",
    border: "var(--color-accent, #d97757)",
  },
  muted: {
    fg: "var(--color-text-subtle, #7e7e84)",
    bg: "var(--color-surface-muted, #222224)",
    border: "var(--color-border, #232325)",
  },
};

function pillStyle(tone: StatusTone): CSSProperties {
  const palette = PALETTE[tone];
  return {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    height: 20,
    padding: "0 8px",
    borderRadius: "var(--radius-full, 999px)",
    backgroundColor: palette.bg,
    color: palette.fg,
    border: `1px solid ${palette.border}`,
    fontSize: "var(--font-size-2xs, 11px)",
    fontWeight: 600,
    letterSpacing: 0.3,
    textTransform: "uppercase",
    whiteSpace: "nowrap",
  };
}

const dotStyle = (tone: StatusTone): CSSProperties => ({
  width: 6,
  height: 6,
  borderRadius: "50%",
  backgroundColor: PALETTE[tone].fg,
  flexShrink: 0,
});

export function StatusPill({
  status,
  label,
  className,
}: StatusPillProps): ReactElement {
  return (
    <span
      style={pillStyle(status)}
      className={className}
      data-testid="status-pill"
      data-status={status}
      aria-label={`Status: ${label}`}
    >
      <span aria-hidden="true" style={dotStyle(status)} />
      {label}
    </span>
  );
}
