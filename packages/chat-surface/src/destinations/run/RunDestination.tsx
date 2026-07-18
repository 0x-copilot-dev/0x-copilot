// RunDestination — the Run cockpit shell (PR-3.5).
//
// Source: docs/plan/desktop-redesign/phase-3/PRD.md (PR-3.5 in §7; FR-3.1 /
// FR-3.2 / FR-3.3) + DESIGN-SPEC.md §2 (Run cockpit layout).
//
// This is the *composition shell*: it wires the three already-merged pieces
// into one cockpit and mounts as the desktop `run` destination —
//
//   - `useRunSession` (PR-3.3): resolves the conversation's active/selected run
//     and streams its events (Transport-port SSE) into an append-only array.
//   - `useRunMode`   (PR-3.4): the KeyValueStore-backed Studio/Focus mode +
//     the global ⌘M toggle (gated to `enabled`, i.e. Run is active).
//   - `ThreadCanvas` (Phase 2): the single-mount, mode-driven canvas — center
//     work surface + chat column + bottom timeline. It projects the session's
//     `events` **once** internally (`useEventProjector`), so the shell does NOT
//     project again — one projection per render (FR-3.3).
//
// The header (`RunHeader`) shows the "ACTIVE RUN" kicker + goal and the
// Studio/Focus segmented control; both the header control and `ThreadCanvas`'s
// `onModeChange` drive the single `useRunMode.setMode`, so every mode affordance
// stays in parity.
//
// SEAMS LEFT FOR THE REST OF PHASE 3 (kept intentionally thin here):
//   - PR-3.6 right rail (DONE): the recomposed `[Chat · Sources · Agents ·
//     Approvals]` `RunWorkspaceRail` now mounts in `ThreadCanvas`'s new
//     `rightRail` slot (replacing its built-in `TcChat` column), and the
//     in-canvas mode switcher is collapsed (`showModeSwitcher={false}`) so
//     `RunHeader` is the single mode control. The Sources/Agents/Approvals
//     tab inputs stay controlled/injected — a later PR / the desktop host
//     threads the reducer outputs; PR-3.6 wires the Chat tab (single TcChat).
//   - PR-3.7 timeline scrub: `scrubbedSeq`/`onScrub`/`onSnapToNow` plumb through
//     `ThreadCanvas`; the shell will own the scrub cursor + the surface tab it
//     snaps to, plus the "Viewing…" banner and composer/approval gating.
//   - PR-3.8 subagents / PR-3.9 streaming / PR-3.10 approvals: consume the same
//     `session.events` projection + the surface `pendingDiff`/approve/reject
//     props `ThreadCanvas` already exposes.
//   - PR-3.11 empty/multi-run: `session.runs` + `session.selectRun` back the
//     `RunMultiSelect`, and `RunEmptyState` (goal composer) mounts when
//     `session.runId === null`; the `runId` prop lets the empty→live start bind
//     to a fresh run without a shell remount (FR-3.25).
//
// Boundary: framework-agnostic. All I/O is port-only — Transport (via
// `useTransport`) + KeyValueStore (inside `useRunMode`); no bare
// window/document/fetch/localStorage (FR-3.27).

import {
  useCallback,
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import type { ConversationId, RunId } from "@0x-copilot/api-types";

import { useTransport } from "../../providers/TransportProvider";
// PR-3.8: pure selector projecting parallel-subagent + fleet state off the
// single canonical event stream (no second subscription / projector).
import { projectSubagents } from "../../subagents";
import { ThreadCanvas, TcChat, type TcTab } from "../../thread-canvas";

import { RunHeader } from "./RunHeader";
import { RunWorkspaceRail } from "./RunWorkspaceRail";
import { useRunMode } from "./useRunMode";
import { useRunSession } from "./useRunSession";

export interface RunDestinationProps {
  /** Conversation whose active/selected run the cockpit binds to. */
  readonly conversationId: ConversationId;
  /**
   * Explicit target run. Wins over auto-resolution and is streamed even before
   * it appears in the run list — the seam PR-3.11 uses to bind the empty→live
   * transition to a freshly-created run without a shell remount (FR-3.25).
   */
  readonly runId?: RunId | null;
  /**
   * Gate the whole cockpit: when `false`, the session neither resolves nor
   * streams and the ⌘M listener is detached (Run is not the active
   * destination). Defaults to `true`. The desktop outlet only mounts this for
   * the `run` slug, so the default is correct there.
   */
  readonly enabled?: boolean;
  /** Agent display name for the header avatar + a11y. */
  readonly agentName?: string;
  /**
   * Override the header goal. When unset, the goal is derived from the selected
   * run's list entry. (PR-3.11 replaces the derived-goal path with the real
   * run selection / empty-state composer.)
   */
  readonly goal?: string | null;
}

export function RunDestination(props: RunDestinationProps): ReactElement {
  const {
    conversationId,
    runId: explicitRunId = null,
    enabled = true,
    agentName,
    goal: goalOverride,
  } = props;

  const transport = useTransport();
  const session = useRunSession({
    conversationId,
    runId: explicitRunId,
    enabled,
  });
  const { mode, setMode } = useRunMode({ conversationId, enabled });

  // Surface-tab strip state. `ThreadCanvas` takes `tabs`/`activeUri` as
  // host-controlled props; the shell owns them so a later PR can populate the
  // strip from the projection / snap it to a scrubbed bead (PR-3.7) without the
  // canvas re-deriving them. In PR-3.5 the strip starts empty and the surface
  // pane shows its adapter placeholder until surfaces stream in.
  const [tabs, setTabs] = useState<readonly TcTab[]>([]);
  const [activeUri, setActiveUri] = useState<string>("");

  const handleActivateTab = useCallback((uri: string): void => {
    setActiveUri(uri);
  }, []);
  const handleCloseTab = useCallback((uri: string): void => {
    setTabs((prev) => prev.filter((tab) => tab.uri !== uri));
    setActiveUri((prev) => (prev === uri ? "" : prev));
  }, []);

  // Goal: explicit override wins, else the selected run's list entry.
  const derivedGoal = useMemo(() => {
    if (goalOverride !== undefined) {
      return goalOverride;
    }
    return (
      session.runs.find((run) => run.runId === session.runId)?.goal ?? null
    );
  }, [goalOverride, session.runs, session.runId]);

  // PR-3.6: the tabbed right rail (Chat · Sources · Agents · Approvals). The
  // single TcChat instance lives in the rail's Chat tab — we build it here and
  // inject it as `chatSlot` so mode/tab switches never spawn a second chat
  // mount (FR-3.9). ThreadCanvas renders this rail in its chat gridArea in
  // place of its built-in TcChat (`rightRail` slot).
  //
  // Sources/Agents/Approvals inputs are host-reducer outputs (the same shapes
  // WorkspacePane consumes). The cockpit shell owns exactly one event source —
  // `useRunSession.events`, projected once inside ThreadCanvas — so we do NOT
  // open a second projection / SSE subscription to feed the rail (FR-3.3). Until
  // the desktop host wires the remaining reducers, the rail renders its per-tab
  // empty copy; the badges light up as data flows in (PR-3.10 approvals). The
  // `chatSlot` is the load-bearing wiring in PR-3.6.

  // PR-3.8: parallel subagents render as THREE views from the ONE canonical
  // event stream (FR-3.17). `projectSubagents` is a pure selector over
  // `session.events` — the same array ThreadCanvas hands to `useEventProjector`
  // — so it opens NO second SSE subscription and NO second `useEventProjector`
  // (FR-3.3). Its output feeds the two consumers that live OUTSIDE ThreadCanvas:
  //   (a) the inline `SubagentFleetCard` in TcChat  → `fleets`
  //   (c) the Agents-tab "N live" count in the rail → `subagents`
  // (b) — one timeline lane per subagent — comes from `TcSwimlanes`' own
  // incremental stream inside ThreadCanvas (PRD §5 / risk R4), keyed off the
  // same `runId`, so all three views stay in parity.
  const subagentProjection = useMemo(
    () => projectSubagents(session.events),
    [session.events],
  );

  const chatSlot = (
    <TcChat
      conversationId={conversationId as unknown as string}
      mode={mode}
      fleets={subagentProjection.fleets}
    />
  );
  const rightRail = (
    <RunWorkspaceRail
      mode={mode}
      chatSlot={chatSlot}
      subagents={subagentProjection.subagents}
    />
  );

  return (
    <div
      data-testid="run-destination"
      data-run-status={session.status}
      data-mode={mode}
      style={rootStyle}
    >
      <RunHeader
        goal={derivedGoal}
        agentName={agentName}
        mode={mode}
        onModeChange={setMode}
      />

      {session.error !== null ? (
        <RunErrorBanner
          message={session.error.message}
          onRetry={session.retry}
        />
      ) : null}

      <div data-testid="run-canvas-slot" style={canvasSlotStyle}>
        <ThreadCanvas
          mode={mode}
          conversationId={conversationId}
          runId={(session.runId as RunId | null) ?? null}
          events={session.events}
          onModeChange={setMode}
          tabs={tabs}
          activeUri={activeUri}
          onActivateTab={handleActivateTab}
          onCloseTab={handleCloseTab}
          transport={transport}
          // PR-3.6: mount the recomposed rail in the chat column, and collapse
          // the canvas's own mode switcher so RunHeader is the single mode
          // control (per the PR-3.5 seam note).
          rightRail={rightRail}
          showModeSwitcher={false}
        />
      </div>
    </div>
  );
}

// ============================================================
// Non-blocking error banner (FR-3.32)
// ============================================================
//
// A run-stream (or run-resolution) failure surfaces here as a `role="alert"`
// strip with **Retry** — it never replaces the cockpit, so the last-projected
// state stays visible while the user re-subscribes.

interface RunErrorBannerProps {
  readonly message: string;
  readonly onRetry: () => void;
}

function RunErrorBanner(props: RunErrorBannerProps): ReactElement {
  const { message, onRetry } = props;
  return (
    <div role="alert" data-testid="run-error-banner" style={errorBannerStyle}>
      <span style={errorTextStyle}>Run stream interrupted — {message}</span>
      <button
        type="button"
        data-testid="run-error-retry"
        onClick={onRetry}
        style={retryButtonStyle}
      >
        Retry
      </button>
    </div>
  );
}

// ============================================================
// Styles (design-system tokens only)
// ============================================================

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  height: "100%",
  minHeight: 0,
  width: "100%",
  background: "var(--color-bg, #0e1015)",
  color: "var(--color-text, #f4f5f6)",
  fontFamily: "var(--font-sans)",
};

const canvasSlotStyle: CSSProperties = {
  flex: 1,
  minHeight: 0,
  position: "relative",
};

const errorBannerStyle: CSSProperties = {
  flexShrink: 0,
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  padding: "8px 16px",
  background: "var(--color-danger-soft, rgba(240,118,79,.12))",
  borderBottom: "1px solid var(--color-danger, #f0764f)",
  color: "var(--color-text, #f4f5f6)",
  fontSize: "var(--font-size-xs, 12px)",
};

const errorTextStyle: CSSProperties = {
  minWidth: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const retryButtonStyle: CSSProperties = {
  flexShrink: 0,
  background: "transparent",
  color: "var(--color-accent, #5fb2ec)",
  border: "1px solid var(--color-accent, #5fb2ec)",
  borderRadius: 6,
  padding: "3px 12px",
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
};
