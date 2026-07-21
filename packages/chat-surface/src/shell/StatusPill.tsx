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
  /**
   * Whether to render the leading status dot. Defaults to `true` (the historic
   * behaviour every caller relied on). The v3 design shows the dot only on LIVE
   * run chips, so `statusTone(...).showDot` should be threaded here for run
   * status chips (PRD-B FR-B.3).
   */
  readonly showDot?: boolean;
}

interface TonePalette {
  readonly fg: string;
  readonly bg: string;
  readonly border: string;
}

// Tone → tokens. No hex fallbacks: the design-system tokens are always defined,
// and the old fallbacks were the stale Claude-terracotta palette (#d97757 etc.),
// which rendered the WRONG colour if a token ever failed to resolve (PRD-B).
const PALETTE: Readonly<Record<StatusTone, TonePalette>> = {
  ok: {
    fg: "var(--color-success)",
    bg: "var(--color-success-bg)",
    border: "var(--color-success)",
  },
  error: {
    fg: "var(--color-danger)",
    bg: "var(--color-danger-bg)",
    border: "var(--color-danger)",
  },
  warning: {
    fg: "var(--color-warning)",
    bg: "var(--color-warning-bg)",
    border: "var(--color-warning)",
  },
  info: {
    fg: "var(--color-accent)",
    bg: "var(--color-bg-accent-subtle)",
    border: "var(--color-accent)",
  },
  muted: {
    fg: "var(--color-text-subtle)",
    bg: "var(--color-surface-muted)",
    border: "var(--color-border)",
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
  showDot = true,
}: StatusPillProps): ReactElement {
  return (
    <span
      style={pillStyle(status)}
      className={className}
      data-testid="status-pill"
      data-status={status}
      aria-label={`Status: ${label}`}
    >
      {showDot ? <span aria-hidden="true" style={dotStyle(status)} /> : null}
      {label}
    </span>
  );
}
