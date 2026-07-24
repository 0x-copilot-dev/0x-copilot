// RunHeader — the Run cockpit's `.ws-head` (PR-3.5).
//
// Source: docs/plan/desktop-redesign/design-reference/DESIGN-SPEC.md §2
//   Header `.ws-head`: agent avatar (Mark), an "ACTIVE RUN" mono kicker + the
//   goal `<h2>`, and a right-aligned **mode segmented control** (Studio / Focus).
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

import { type RunMode, STUDIO_ENABLED } from "./useRunMode";

/** Canonical order for the segmented control + arrow-key cycling. */
const MODE_ORDER: readonly RunMode[] = ["studio", "focus"];

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
  /** Agent display name — seeds the avatar glyph + a11y label. */
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
  const avatarGlyph = (agentName.trim()[0] ?? "A").toUpperCase();

  return (
    <header data-testid="run-header" style={headerStyle}>
      <div
        aria-hidden="true"
        data-testid="run-header-avatar"
        style={avatarStyle}
      >
        {avatarGlyph}
      </div>
      <div style={headingBlockStyle}>
        <span data-testid="run-header-kicker" style={kickerStyle}>
          {resolvedKicker}
        </span>
        <div style={goalRowStyle}>
          <h2 data-testid="run-header-goal" style={goalStyle}>
            {goalText}
          </h2>
          <RunStatusPulse runStatus={runStatus} />
          {status !== undefined && status !== null ? (
            <span data-testid="run-header-status">{status}</span>
          ) : null}
        </div>
      </div>
      {/* Studio disabled ⇒ Focus-only: no Studio/Focus switcher to show. */}
      {STUDIO_ENABLED ? (
        <ModeSegmentedControl
          agentName={agentName}
          mode={mode}
          onModeChange={onModeChange}
        />
      ) : null}
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
}

function ModeSegmentedControl(props: ModeSegmentedControlProps): ReactElement {
  const { mode, onModeChange } = props;

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
            style={segmentButtonStyle(selected)}
          >
            {label}
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
}: {
  readonly runStatus: AgentRunStatus | null;
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
      {label}
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
  flexShrink: 0,
  display: "flex",
  alignItems: "center",
  gap: 12,
  padding: "12px 16px",
  borderBottom: "1px solid var(--color-border, #22252e)",
  background: "var(--color-bg-elevated, #16181f)",
  color: "var(--color-text, #f4f5f6)",
  fontFamily: "var(--font-sans)",
};

const avatarStyle: CSSProperties = {
  flexShrink: 0,
  width: 32,
  height: 32,
  borderRadius: "50%",
  display: "grid",
  placeItems: "center",
  background: "var(--color-accent-soft, rgba(95,178,236,.10))",
  color: "var(--color-accent, #5fb2ec)",
  border: "1px solid var(--color-accent-line, rgba(95,178,236,.35))",
  fontWeight: 600,
  fontSize: "var(--font-size-sm, 13px)",
};

const headingBlockStyle: CSSProperties = {
  flex: 1,
  minWidth: 0,
  display: "flex",
  flexDirection: "column",
  gap: 2,
};

const kickerStyle: CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-2xs, 11px)",
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color: "var(--color-text-muted, #9aa0a6)",
};

const goalRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  minWidth: 0,
};

const goalStyle: CSSProperties = {
  margin: 0,
  fontFamily: "var(--font-display, var(--font-sans))",
  fontSize: "var(--font-size-md, 15px)",
  fontWeight: 600,
  letterSpacing: "-0.01em",
  color: "var(--color-text, #f4f5f6)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const segmentedStyle: CSSProperties = {
  flexShrink: 0,
  display: "flex",
  gap: 4,
  padding: 3,
  borderRadius: 999,
  background: "var(--color-bg, #0e1015)",
  border: "1px solid var(--color-border, #2a2d31)",
};

const segmentButtonStyle = (selected: boolean): CSSProperties => ({
  background: selected ? "var(--color-accent, #5fb2ec)" : "transparent",
  color: selected
    ? "var(--color-accent-contrast, #08131d)"
    : "var(--color-text-muted, #9aa0a6)",
  border: "1px solid transparent",
  borderRadius: 999,
  padding: "4px 14px",
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  cursor: "pointer",
  outline: "none",
  fontFamily: "inherit",
});
