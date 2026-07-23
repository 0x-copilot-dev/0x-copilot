// <StatusPill status label /> — one component, one tone-to-recipe mapping.
//
// Source: cross-audit.md §1.6 + destinations-master-prd §4.2. Every
// destination's status-coloured chip flows through this primitive; no
// per-destination color choices.
//
// This is a thin adapter over the design-system `.ui-badge` recipe (the
// design's `.chip` — mono, outlined, NO fill; packages/design-system/src/
// styles.css). It carries ZERO style of its own: it maps the five semantic
// tones to the recipe's tone classes and renders the live-status dot slot.
// The `.ui-badge` recipe is the single source of truth for the chip's shape;
// `<Badge>` (design-system) renders the SAME classes for design-system-native
// callers. StatusPill applies the classes directly rather than importing
// `<Badge>` so the whole thing is host-local — one import graph, no coupling to
// design-system's component surface for what is a pure class mapping.
//
// The five tones cover every status across destinations (run state, connector
// health, approval state, etc). When a destination wants a sixth tone, extend
// the union here — never inline a new color.

import type { ReactElement } from "react";

export type StatusTone = "ok" | "error" | "warning" | "info" | "muted";

export interface StatusPillProps {
  readonly status: StatusTone;
  readonly label: string;
  readonly className?: string;
  /**
   * Whether to render the leading status dot. Defaults to `false` — the v3
   * design draws the dot (`.dotk`) only on the LIVE run chip, so
   * `statusTone(...).showDot` is threaded here for run-status chips and every
   * other caller renders dot-free (PRD-02 / PRD-B FR-B.3).
   */
  readonly showDot?: boolean;
}

// Tone → `.ui-badge` recipe class. The recipe recolours text + border per tone;
// no fill, no per-tone logic here beyond the class name.
const TONE_CLASS: Readonly<Record<StatusTone, string>> = {
  ok: "ui-badge--success",
  error: "ui-badge--danger",
  warning: "ui-badge--warning",
  info: "ui-badge--accent",
  muted: "ui-badge--muted",
};

export function StatusPill({
  status,
  label,
  className,
  showDot = false,
}: StatusPillProps): ReactElement {
  const classes = ["ui-badge", TONE_CLASS[status]];
  if (className !== undefined && className.length > 0) classes.push(className);
  return (
    <span
      className={classes.join(" ")}
      data-testid="status-pill"
      data-status={status}
      aria-label={`Status: ${label}`}
    >
      {showDot ? <span className="ui-badge__dot" aria-hidden="true" /> : null}
      {label}
    </span>
  );
}
