// RunHeader — the desktop Run cockpit's window bar (PR-3.5).
//
// Source: docs/plan/desktop-redesign/design-reference/DESIGN-SPEC.md §2
//   Header `.mw-bar`: macOS traffic-light dots, the centred `0xCopilot — mode`
//   identity, and a right-aligned **mode segmented control** (Focus / Studio).
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
  const modeLabel = MODE_LABELS[mode];

  return (
    <header data-testid="run-header" style={headerStyle}>
      <WindowDots />
      <div data-testid="run-header-title" style={titleLayerStyle}>
        <b style={productNameStyle}>
          <span style={productMarkStyle}>0x</span>Copilot
        </b>
        <span aria-hidden="true">—</span>
        <span>{modeLabel}</span>
      </div>
      {/* Preserve the run’s useful semantic summary without competing with the
          authoritative compact window-bar composition. The visually rendered
          identity is the product + selected workspace mode; assistive tech
          retains the active/standby state, goal, and live run status. */}
      <div style={visuallyHiddenStyle}>
        <span data-testid="run-header-kicker">{resolvedKicker}</span>
        <h2 data-testid="run-header-goal">{goalText}</h2>
        <RunStatusPulse runStatus={runStatus} />
        {status !== undefined && status !== null ? (
          <span data-testid="run-header-status">{status}</span>
        ) : null}
      </div>
      <ModeSegmentedControl
        agentName={agentName}
        mode={mode}
        onModeChange={onModeChange}
      />
    </header>
  );
}

function WindowDots(): ReactElement {
  return (
    <div
      aria-hidden="true"
      data-testid="run-header-window-dots"
      style={windowDotsStyle}
    >
      <span style={{ ...windowDotStyle, background: "#ff5f57" }} />
      <span style={{ ...windowDotStyle, background: "#febc2e" }} />
      <span style={{ ...windowDotStyle, background: "#28c840" }} />
    </div>
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

const windowDotsStyle: CSSProperties = {
  zIndex: 2,
  display: "flex",
  gap: 8,
  alignItems: "center",
};

const windowDotStyle: CSSProperties = {
  width: 11,
  height: 11,
  borderRadius: "50%",
  border: "0.5px solid rgba(0,0,0,.2)",
};

const titleLayerStyle: CSSProperties = {
  position: "absolute",
  inset: 0,
  top: 0,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  gap: 7,
  fontSize: 12,
  color: "var(--color-text-muted)",
  pointerEvents: "none",
};

const productNameStyle: CSSProperties = {
  color: "var(--color-text-secondary, var(--color-text))",
  fontWeight: 600,
};

const productMarkStyle: CSSProperties = {
  color: "var(--color-accent)",
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

const segmentButtonStyle = (selected: boolean): CSSProperties => ({
  background: selected ? "var(--color-surface-elevated)" : "transparent",
  color: selected ? "var(--color-text)" : "var(--color-text-muted)",
  border: 0,
  borderRadius: 5,
  padding: "5px 12px",
  fontSize: 12,
  fontWeight: 500,
  cursor: "pointer",
  outline: "none",
  fontFamily: "inherit",
});
