// RunHeader — the desktop Run cockpit's window bar (PR-3.5, revised by PRD-02).
//
// Source: docs/plan/desktop-redesign/design-reference/DESIGN-SPEC.md §2 +
//         docs/plan/windowed-mode/PRD-02-run-header.md
//
// Layout, left to right:
//   [leading slot] [ goal ……… ] [scrub label] [● working] [ Focus | Studio ]
//
// PRD-02 replaced the original composition, which was a centred
// `0xCopilot — {mode}` overlay with the goal, kicker, status pulse and scrub
// label all rendered into `visuallyHiddenStyle`'s 1x1 `clip()` box. That bar
// stated the app's name (which the user knows) and the mode (which the
// segmented control on the same row shows selected), while the one fact only
// this bar could carry — what the run is doing — was computed and invisible.
//
// The wordmark is gone entirely rather than width-gated: the desktop host
// already sets the OS window title (`apps/desktop/main/window.ts:32`), and the
// app rail's `BrandMark` carries visible brand presence. A third copy on a 38px
// row bought nothing. (Note the host uses `titleBarStyle: "hiddenInset"`, so
// that title serves the OS — ⌘-tab, Mission Control — not the drawn frame.)
//
// Native window controls belong to the desktop host, never to this surface.
//
// Ownership: RunHeader is presentation only. The *mode value* is owned by
// `useRunMode` (KeyValueStore-backed); this component renders the current mode
// and calls `onModeChange` — the RunDestination shell wires that to
// `useRunMode.setMode`, so the header, the ⌘M chord, and `ThreadCanvas.mode`
// all read/write one source of truth for the `"studio" | "focus"` union.
//
// The segmented control is a two-`role="tab"` tablist (mirroring
// `ThreadCanvas`'s in-canvas switcher): `aria-selected`, roving `tabIndex`, and
// ArrowLeft/ArrowRight cycling over the two values — FR-3.6 / FR-3.29. Tokens
// only (sky accent), no hardcoded palette — FR-3.24 / FR-3.30.

import {
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
  type ReactNode,
} from "react";

import type { AgentRunStatus } from "@0x-copilot/api-types";

import type { RunMode } from "./useRunMode";

/** Canonical order for the segmented control + arrow-key cycling. */
// The design presents the compact reading mode first, followed by the active
// workspace. Studio remains the persisted/default mode; this only fixes the
// visual/control order to match the cockpit chrome.
const MODE_ORDER: readonly RunMode[] = ["focus", "studio"];

const MODE_LABELS: Record<RunMode, string> = {
  studio: "Studio",
  focus: "Focus",
};

/** Kicker shown when no run is active — the header must NOT claim "ACTIVE RUN".
 *  Complements (never duplicates) the empty-state card's "NO ACTIVE RUN". */
const IDLE_KICKER = "STANDBY";
/** Goal-line copy when no run is active: a calm standby posture that is honest
 *  in every idle sub-state (ready, setup-required, submitting) and never a
 *  verbatim echo of the empty-state card's "NO ACTIVE RUN". */
const IDLE_GOAL_COPY = "Standing by";

export interface RunHeaderProps {
  /**
   * The active run's goal — rendered as the header title. `null`/empty falls
   * back to the idle copy so the header never renders a blank `<h2>`. (The
   * dedicated empty/idle goal composer is PR-3.11's `RunEmptyState`; this is
   * just the safe header fallback.)
   */
  readonly goal?: string | null;
  /**
   * Mono kicker above the goal. When unset it is state-aware: "ACTIVE RUN" with
   * a live goal, "STANDBY" when idle — so the header never claims a run it does
   * not have (DESIGN-SPEC §2). An explicit value overrides both states.
   */
  readonly kicker?: string;
  /**
   * Retained for API compatibility with existing RunDestination callers. The
   * compact desktop bar intentionally uses product identity rather than an
   * agent avatar; the active run is still exposed in the accessible summary.
   */
  readonly agentName?: string;
  /** Current layout mode (drives the segmented control's selected tab). */
  readonly mode: RunMode;
  /** Fired when the user picks a mode; wired to `useRunMode.setMode`. */
  readonly onModeChange: (mode: RunMode) => void;
  /**
   * Seam (PR-3.7 timeline / PR-3.9 streaming): an optional status node rendered
   * beside the goal — e.g. the `VIEWING 11:43` scrub label. Unset in PR-3.5.
   */
  readonly status?: ReactNode;
  /**
   * WC-P6b — the bound run's own status, threaded from `useRunSession.runStatus`.
   * A live/active run (queued · running · waiting · cancelling) renders the
   * pulsing `● working` chip beside the goal (DESIGN-SPEC §2 `.ws-side` header);
   * a terminal run (or `null`) renders nothing, so the header stops pulsing the
   * moment the run settles. Pure presentation — the value is derived upstream
   * from the single event projection, never a second subscription (FR-3.3).
   */
  readonly runStatus?: AgentRunStatus | null;
  /**
   * PRD-01 D-1.3 — leading slot at the LEFT of the bar, before the centred
   * identity layer. The cockpit mounts its Threads toggle here, matching where
   * both reference products put their sidebar control.
   *
   * The slot exists because the left of this bar was empty: the old identity
   * layer was an absolutely-positioned, `pointer-events: none` overlay across
   * the whole header, and the mode control is `margin-left: auto` on the right.
   * Rendered only when supplied, so a header without it is byte-identical.
   */
  readonly leading?: ReactNode;
  /**
   * PRD-02 D-2.2 — the surface is narrow. Drops the pulse chip's LABEL (the dot
   * survives, still announced) and shortens the mode control to single letters.
   * The goal keeps the whole remaining row.
   *
   * A prop, not a `useShellWidthClass()` call: this component is presentation
   * only, and a test must be able to render both widths without a provider.
   */
  readonly compact?: boolean;
}

/** Active (in-flight) run states that pulse the header dot; every other status
 *  (or `null`) is settled and shows no dot. `cancelling` still counts as active
 *  — the run is winding down, not done. Mirrors the cockpit's cancellable set. */
const ACTIVE_PULSE_STATUSES: ReadonlySet<AgentRunStatus> = new Set([
  "queued",
  "running",
  "waiting_for_approval",
  "cancelling",
]);

/** Per-state label for the pulse chip — the design's `● working` chip, honest in
 *  each active sub-state so the header never says "working" while queued/waiting. */
const PULSE_LABELS: Partial<Record<AgentRunStatus, string>> = {
  queued: "queued",
  running: "working",
  waiting_for_approval: "waiting",
  cancelling: "cancelling",
};

const ACTIVE_KICKER = "ACTIVE RUN";
const DEFAULT_AGENT_NAME = "Agent";

export function RunHeader(props: RunHeaderProps): ReactElement {
  const {
    goal,
    kicker,
    agentName = DEFAULT_AGENT_NAME,
    mode,
    onModeChange,
    status,
    runStatus = null,
    leading,
    compact = false,
  } = props;

  // A run is "active" only when it carries a real goal. Deriving BOTH the goal
  // line and the kicker from this one fact is what stops the header from ever
  // claiming "ACTIVE RUN" while showing idle copy.
  const activeGoal =
    goal !== null && goal !== undefined && goal.trim() !== "" ? goal : null;
  const goalText = activeGoal ?? IDLE_GOAL_COPY;
  // State-aware kicker: "ACTIVE RUN" with a live goal, idle kicker otherwise.
  // An explicit `kicker` prop overrides both states.
  const resolvedKicker =
    kicker ?? (activeGoal !== null ? ACTIVE_KICKER : IDLE_KICKER);

  return (
    <header data-testid="run-header" style={headerStyle}>
      {leading !== undefined && leading !== null ? (
        <div data-testid="run-header-leading" style={leadingSlotStyle}>
          {leading}
        </div>
      ) : null}

      {/* PRD-02 D-2.1 — the GOAL is the title.
          The bar used to render a centred `0xCopilot — {mode}` overlay while the
          goal, kicker, pulse and scrub label all sat in a 1x1 `clip()` box. Two
          of those three facts were already on screen (the app's name, and the
          mode the segmented control shows selected); the one thing only this bar
          could say was invisible. */}
      <h2
        data-testid="run-header-goal"
        style={goalStyle(activeGoal === null)}
        title={goalText}
      >
        {goalText}
      </h2>

      {/* D-2.5 — a user looking at the past must be TOLD, in chrome. The
          scrubber's own `↩ Now` pill is too quiet for a mode this consequential
          (PRD-08 D-8.6 adds the second signal on the strip itself). */}
      {status !== undefined && status !== null ? (
        <span data-testid="run-header-status" style={statusSlotStyle}>
          {status}
        </span>
      ) : null}

      {/* The "● WAITING / ● WORKING" pulse is GONE from the header. The canvas
          already states the run's condition in words a person can act on
          ("Waiting for your approval or access"), the tool cards carry their own
          per-step status, and the composer's send button reflects in-flight. A
          fourth copy in the chrome was the least specific of the four. */}

      {/* D-2.6 — the kicker has no visible equivalent now that the goal is the
          title, so it stays for assistive tech. NOTHING rendered visibly is
          duplicated here: the goal used to be announced twice. */}
      <span style={visuallyHiddenStyle} data-testid="run-header-kicker">
        {resolvedKicker}
      </span>

      <ModeSegmentedControl
        agentName={agentName}
        mode={mode}
        onModeChange={onModeChange}
        compact={compact}
      />
    </header>
  );
}

// ============================================================
// Mode segmented control
// ============================================================

interface ModeSegmentedControlProps {
  readonly agentName: string;
  readonly mode: RunMode;
  readonly onModeChange: (mode: RunMode) => void;
  readonly compact?: boolean;
}

function ModeSegmentedControl(props: ModeSegmentedControlProps): ReactElement {
  const { mode, onModeChange, compact = false } = props;

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
      return;
    }
    event.preventDefault();
    const idx = MODE_ORDER.indexOf(mode);
    if (idx < 0) {
      return;
    }
    const dir = event.key === "ArrowLeft" ? -1 : 1;
    const next = (idx + dir + MODE_ORDER.length) % MODE_ORDER.length;
    onModeChange(MODE_ORDER[next]);
  };

  return (
    <div
      role="tablist"
      aria-label="Run cockpit mode"
      data-testid="run-mode-switcher"
      style={segmentedStyle}
      onKeyDown={handleKeyDown}
    >
      {MODE_ORDER.map((value) => {
        const selected = value === mode;
        const label = MODE_LABELS[value];
        // D-2.2 — single letters at compact. `aria-label` below still carries
        // the full "Focus mode" / "Studio mode", so nothing is lost to AT.
        const shown = compact ? label.charAt(0) : label;
        return (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-label={`${label} mode`}
            tabIndex={selected ? 0 : -1}
            data-testid={`run-mode-${value}`}
            data-mode-value={value}
            onClick={() => onModeChange(value)}
            style={segmentButtonStyle(selected, compact)}
          >
            {shown}
          </button>
        );
      })}
    </div>
  );
}

// ============================================================
// Run status pulse (WC-P6b)
// ============================================================
//
// The design's `● working` chip: a sky-accent dot that pulses while the run is
// in flight, plus a per-state label. Terminal / null → the whole chip is absent,
// so the header stops pulsing the instant the run settles. The pulse ring lives
// in a scoped `<style>` (the package owns no keyframe primitive — same pattern
// as ConnectModal / AddProviderKeyModal) and is zeroed under reduced-motion so
// it honours `prefers-reduced-motion` and the app's `[data-reduce-motion]` gate
// (FR-3.24 checklist).

const PULSE_STYLE = `
.run-header-pulse-dot {
  animation: run-header-pulse 1.6s ease-out infinite;
}
@keyframes run-header-pulse {
  0% { box-shadow: 0 0 0 0 var(--color-accent-soft, rgba(95,178,236,.45)); }
  70% { box-shadow: 0 0 0 5px rgba(95,178,236,0); }
  100% { box-shadow: 0 0 0 0 rgba(95,178,236,0); }
}
[data-reduce-motion="always"] .run-header-pulse-dot { animation: none; }
@media (prefers-reduced-motion: reduce) { .run-header-pulse-dot { animation: none; } }
`;

function RunStatusPulse({
  runStatus,
  compact = false,
}: {
  readonly runStatus: AgentRunStatus | null;
  readonly compact?: boolean;
}): ReactElement | null {
  if (runStatus === null || !ACTIVE_PULSE_STATUSES.has(runStatus)) {
    return null;
  }
  const label = PULSE_LABELS[runStatus] ?? "working";
  return (
    <span
      data-testid="run-header-status-pulse"
      data-run-status={runStatus}
      style={pulseChipStyle}
    >
      <style>{PULSE_STYLE}</style>
      <span
        aria-hidden="true"
        className="run-header-pulse-dot"
        data-testid="run-header-pulse-dot"
        style={pulseDotStyle}
      />
      {/* At compact the label is visually hidden rather than removed, so the
          state is still announced — the dot alone means nothing to a reader. */}
      <span style={compact ? visuallyHiddenStyle : undefined}>{label}</span>
    </span>
  );
}

const pulseChipStyle: CSSProperties = {
  flexShrink: 0,
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-2xs, 11px)",
  letterSpacing: "0.06em",
  textTransform: "uppercase",
  color: "var(--color-text-muted, #9aa0a6)",
};

const pulseDotStyle: CSSProperties = {
  width: 7,
  height: 7,
  borderRadius: "50%",
  background: "var(--color-accent, #5fb2ec)",
};

// ============================================================
// Styles (design-system tokens only)
// ============================================================

const headerStyle: CSSProperties = {
  boxSizing: "border-box",
  position: "relative",
  flexShrink: 0,
  display: "flex",
  alignItems: "center",
  gap: 12,
  height: 38,
  padding: "0 13px",
  borderBottom: "1px solid var(--color-border)",
  background: "var(--color-bg-elevated)",
  color: "var(--color-text)",
  fontFamily: "var(--font-sans)",
};

const leadingSlotStyle: CSSProperties = {
  position: "relative",
  zIndex: 1,
  flexShrink: 0,
  display: "flex",
  alignItems: "center",
};

/**
 * The goal line. Deliberately the SAME recipe as `Topbar.tsx`'s `titleStyle`
 * (--font-display / --font-size-sm / semibold / -0.01em / lh 1.2 / ellipsis):
 * the shell already had one header-title treatment, and minting a second would
 * be the drift this codebase keeps paying for.
 *
 * `flex: 1` + `minWidth: 0` is what lets the goal ellipsise instead of shoving
 * the status and mode clusters off the row — those are `flex: none` (FR-2.7).
 */
const goalStyle = (idle: boolean): CSSProperties => ({
  flex: 1,
  minWidth: 0,
  margin: 0,
  fontFamily: "var(--font-display)",
  fontSize: "var(--font-size-sm)",
  fontWeight: (idle
    ? 500
    : "var(--font-weight-semibold)") as CSSProperties["fontWeight"],
  letterSpacing: "-0.01em",
  lineHeight: 1.2,
  // Idle is quieter: "Standing by" is a state, not a thing the user named.
  color: idle ? "var(--color-text-muted)" : "var(--color-text)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
});

/** The scrub label sits beside the goal and never shrinks. */
const statusSlotStyle: CSSProperties = {
  flex: "none",
  display: "inline-flex",
  alignItems: "center",
  minWidth: 0,
};

const visuallyHiddenStyle: CSSProperties = {
  position: "absolute",
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: "hidden",
  clip: "rect(0, 0, 0, 0)",
  whiteSpace: "nowrap",
  border: 0,
};

const segmentedStyle: CSSProperties = {
  flexShrink: 0,
  display: "flex",
  gap: 2,
  marginLeft: "auto",
  padding: 2,
  borderRadius: 7,
  background: "var(--color-surface)",
  border: "1px solid var(--color-border)",
};

const segmentButtonStyle = (
  selected: boolean,
  compact = false,
): CSSProperties => ({
  background: selected ? "var(--color-surface-elevated)" : "transparent",
  color: selected ? "var(--color-text)" : "var(--color-text-muted)",
  border: 0,
  borderRadius: 5,
  padding: compact ? "5px 8px" : "5px 12px",
  fontSize: 12,
  fontWeight: 500,
  cursor: "pointer",
  outline: "none",
  fontFamily: "inherit",
});
