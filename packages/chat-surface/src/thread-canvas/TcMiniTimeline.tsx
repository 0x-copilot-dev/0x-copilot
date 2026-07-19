// Mini-timeline scrubber for Focus / Studio-minimized modes.
//
// Source: chats-canvas-prd.md §3.1 + §3.7 (binding 2026-05-17). The mini
// timeline is a thin strip of color-coded beads with a "Now" pill and an
// expand chevron. Click a bead → scrub there. Click ↩ Now → snap live.
// Click ↑ → emit `onExpand` (caller flips mode back to Studio).
//
// The scrubber drives client-side time-travel: it does NOT call a backend
// snapshot endpoint. The consumer (ThreadCanvas → TcSurfaceMount) handles
// the actual state-rewind via `eventProjector.projectAt()` — see
// `TcSurfaceMount.reduceTo`.
//
// Composability: the mini-timeline is a stateless projection-renderer.
// It receives the beads array (already projected) and a `scrubbedTo`
// cursor. It owns no state of its own; every change exits via callbacks.

import {
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
} from "react";

import type { TimelineBead } from "./eventProjector";

export interface TcMiniTimelineProps {
  /** Projected beads, in ascending `sequence_no` order. */
  readonly beads: readonly TimelineBead[];
  /**
   * Current scrub cursor. `null` means "live / now"; the cursor sits at
   * the right edge of the strip.
   */
  readonly scrubbedTo: number | null;
  /**
   * Called when the user clicks a bead. The argument is the bead's
   * `sequence_no`. The host translates this into a `projectAt()` call.
   */
  readonly onScrub: (sequenceNo: number) => void;
  /** Called when the user clicks ↩ Now (or presses Escape on the strip). */
  readonly onSnapToNow: () => void;
  /**
   * Called when the user clicks the expand chevron. Hosts typically map
   * this to "switch back to Studio mode".
   */
  readonly onExpand?: () => void;
}

const LANE_COLORS = new Map<string, string>([
  ["email", "var(--color-accent, #d97757)"],
  ["sheet", "var(--color-success, #6ab04c)"],
  ["sf-opp", "var(--color-warning, #f0a330)"],
  ["slide", "var(--color-info, #7a9bd9)"],
  ["system", "var(--color-text-subtle, #7e7e84)"],
]);

function colorForLane(lane: string): string {
  return LANE_COLORS.get(lane) ?? "var(--color-text-muted, #b6b6bc)";
}

export function TcMiniTimeline(props: TcMiniTimelineProps): ReactElement {
  const { beads, scrubbedTo, onScrub, onSnapToNow, onExpand } = props;
  const isLive = scrubbedTo === null;
  const isEmpty = beads.length === 0;

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (beads.length === 0) {
      return;
    }
    // PR-3.7 (FR-3.14): ⌘L / Ctrl+L snaps to now, alongside Escape. `⌘←`/`⌘→`
    // step (the ArrowLeft/ArrowRight branch below already ignores modifiers, so
    // the Meta-modified chords fall through to it).
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "l") {
      event.preventDefault();
      onSnapToNow();
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      onSnapToNow();
      return;
    }
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
      return;
    }
    event.preventDefault();
    const currentIdx =
      scrubbedTo === null
        ? beads.length - 1
        : beads.findIndex((b) => b.sequenceNo === scrubbedTo);
    const dir = event.key === "ArrowLeft" ? -1 : 1;
    const nextIdx = Math.max(0, Math.min(beads.length - 1, currentIdx + dir));
    if (nextIdx === beads.length - 1 && dir === 1) {
      onSnapToNow();
      return;
    }
    onScrub(beads[nextIdx].sequenceNo);
  };

  return (
    <div
      data-testid="tc-mini-timeline"
      data-state={isLive ? "live" : "scrubbed"}
      data-empty={isEmpty ? "true" : "false"}
      style={isEmpty ? emptyContainerStyle : containerStyle}
      tabIndex={isEmpty ? -1 : 0}
      role="region"
      aria-label="Run timeline (mini)"
      onKeyDown={handleKeyDown}
    >
      <div style={beadStripStyle} role="presentation">
        {beads.length === 0 ? (
          <span
            data-testid="tc-mini-timeline-empty"
            style={emptyStyle}
            role="status"
          >
            No activity yet
          </span>
        ) : (
          beads.map((bead) => {
            const selected = scrubbedTo === bead.sequenceNo;
            return (
              <button
                key={bead.id}
                type="button"
                data-testid={`tc-mini-timeline-bead-${bead.id}`}
                data-selected={selected ? "true" : "false"}
                data-pending={bead.pending ? "true" : "false"}
                aria-label={bead.title}
                aria-pressed={selected}
                title={bead.title}
                onClick={() => onScrub(bead.sequenceNo)}
                style={beadStyle(
                  colorForLane(bead.lane),
                  selected,
                  bead.pending,
                )}
              />
            );
          })
        )}
      </div>
      <div style={controlsRowStyle}>
        {/* Progressive disclosure: an empty timeline is permanently "Live" and
            snap-to-now is a no-op, so the pill is dead chrome — withhold it
            until the first bead arrives. */}
        {!isEmpty ? (
          <button
            type="button"
            data-testid="tc-mini-timeline-now"
            aria-pressed={isLive}
            onClick={onSnapToNow}
            style={pillStyle(isLive)}
          >
            {isLive ? "Live" : "↩ Now"}
          </button>
        ) : null}
        {onExpand ? (
          <button
            type="button"
            data-testid="tc-mini-timeline-expand"
            aria-label="Expand timeline (Studio mode)"
            onClick={onExpand}
            style={expandStyle}
          >
            ↑
          </button>
        ) : null}
      </div>
    </div>
  );
}

const containerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "6px 10px",
  background: "var(--color-bg-elevated)",
  borderTop: "1px solid var(--color-border)",
  color: "var(--color-text-muted)",
  fontFamily: "var(--font-sans)",
  fontSize: "var(--font-size-2xs)",
};

// Progressive disclosure: with zero beads there is nothing to scrub, so the
// strip recedes to a quiet status line — transparent fill, hairline top rule,
// subtler text — instead of presenting as an interactive transport bar. Same
// quiet-chrome recipe as the web composer hint row (styles.css:2185-2214).
const emptyContainerStyle: CSSProperties = {
  ...containerStyle,
  background: "transparent",
  borderTop:
    "1px solid color-mix(in srgb, var(--color-border) 40%, transparent)",
  color: "var(--color-text-subtle)",
  opacity: 0.8,
};

const beadStripStyle: CSSProperties = {
  flex: 1,
  display: "flex",
  alignItems: "center",
  gap: 4,
  minWidth: 0,
  overflowX: "auto",
};

const emptyStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
  fontSize: "var(--font-size-2xs)",
};

const beadStyle = (
  color: string,
  selected: boolean,
  pending: boolean,
): CSSProperties => ({
  width: selected ? 12 : 8,
  height: selected ? 12 : 8,
  borderRadius: "50%",
  background: color,
  border: pending
    ? "1.5px solid var(--color-warning, #f0a330)"
    : selected
      ? "1.5px solid var(--color-text, #e8e8eb)"
      : "1.5px solid transparent",
  padding: 0,
  cursor: "pointer",
  flexShrink: 0,
});

const controlsRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 4,
  flexShrink: 0,
};

const pillStyle = (live: boolean): CSSProperties => ({
  background: live ? "var(--color-surface-muted)" : "var(--color-accent)",
  color: live ? "var(--color-text-muted)" : "var(--color-accent-contrast)",
  border: "1px solid var(--color-border)",
  borderRadius: 999,
  padding: "2px 10px",
  fontSize: "var(--font-size-2xs)",
  cursor: "pointer",
  fontFamily: "inherit",
});

const expandStyle: CSSProperties = {
  background: "transparent",
  color: "var(--color-text-muted)",
  border: "1px solid var(--color-border)",
  borderRadius: 6,
  padding: "2px 6px",
  fontSize: "var(--font-size-2xs)",
  cursor: "pointer",
  fontFamily: "inherit",
};
