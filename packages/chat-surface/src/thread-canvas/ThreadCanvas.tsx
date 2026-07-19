// ThreadCanvas — single-mount, mode-driven slot host.
//
// Source: chats-canvas-prd.md §3.1 + §3.2 + §3.3 (binding 2026-05-17).
//
// THE INVARIANT THAT GOVERNS THIS FILE
// =====================================
// ThreadCanvas mounts ONCE per (component-instance). The two modes
// (Studio / Focus) are presentation slots — NOT separate canvases.
// Switching modes MUST NOT remount any of the inner components
// (TcSurfaceMount, TcSwimlanes, TcChat, TcMiniTimeline, Composer).
// Anything mounted in two modes survives the switch because the JSX is
// the same React-element shape across renders; React reconciliation
// preserves it.
//
// Implementation rule: every per-mode rendering decision is a
// conditional ON ATTRIBUTES (visibility, display, presence-in-tree of
// optional secondary slots), NOT a wholesale tree swap. Where a slot
// is absent in a mode, we use `{condition && <X />}` for the secondary
// elements that don't exist in that mode; the primary elements
// (TcSurfaceMount + TcChat) are always rendered, with their host slot
// hidden via CSS in modes that don't show them. This guarantees zero
// remounts across the four state-survival contracts in §3.3:
//
//   - Chat scroll position
//   - Active app tab (TcTabs)
//   - Scrub position (forwarded via SwimlaneScrubProvider)
//   - Composer draft (Composer is a stable instance inside TcChat)
//
// THE EVENT PROJECTOR
// ===================
// One `eventProjector` runs ONCE per render via `useEventProjector`.
// Four consumer-shapes (surface / swimlanes / chat / timeline) read
// slices off the projection. A second projection of the same envelopes
// elsewhere is a bug — converge on this hook.
//
// MODE STORAGE
// ============
// This component does NOT persist mode. The host (ChatScreen) wires
// `mode` from KV and writes via `onModeChange`. The mode-switcher
// tablist at the top of the canvas calls `onModeChange` only — the
// canvas itself is controlled.

import {
  useMemo,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
  type ReactNode,
} from "react";

import type {
  ConversationId,
  RunId,
  RuntimeEventEnvelope,
} from "@0x-copilot/api-types";
import type { Transport } from "@0x-copilot/chat-transport";

import { SwimlaneScrubProvider } from "./SwimlaneScrubContext";
import { TcChat } from "./TcChat";
import { TcMiniTimeline } from "./TcMiniTimeline";
import { TcSurfaceMount, type PendingDiffHandle } from "./TcSurfaceMount";
import { TcSwimlanes } from "./TcSwimlanes";
import { TcTabs, type TcTab } from "./TcTabs";
import { useEventProjector } from "./useEventProjector";

export type ThreadMode = "studio" | "focus";

const MODE_VALUES: readonly ThreadMode[] = ["studio", "focus"];

export interface ThreadCanvasProps {
  /** Current presentation slot. */
  readonly mode: ThreadMode;
  /** Stable conversation identity. Threaded through to TcChat. */
  readonly conversationId: ConversationId;
  /**
   * Active run id (or `null` when no run has started yet). When `null`,
   * the swimlanes slot does NOT render (TcSwimlanes requires a run).
   */
  readonly runId: RunId | null;
  /**
   * Append-only runtime event envelopes in ascending sequence order.
   * Sourced from the host's SSE subscription (see PRD §3.2). Defaults
   * to `[]` so tests that don't pass events still mount cleanly.
   */
  readonly events?: readonly RuntimeEventEnvelope[];
  /** Mode-switch callback fired when the user picks a different mode. */
  readonly onModeChange: (mode: ThreadMode) => void;
  /** Surface-tab strip data. */
  readonly tabs: readonly TcTab[];
  readonly activeUri: string;
  readonly onActivateTab: (uri: string) => void;
  readonly onCloseTab: (uri: string) => void;
  /** Transport for surface mount + provider-less callsites. */
  readonly transport: Transport;
  /** Pending diff overlay for the active surface. */
  readonly pendingDiff?: PendingDiffHandle | null;
  readonly onApprove?: (diffId: string) => void;
  readonly onReject?: (diffId: string) => void;
  readonly onSuggestChanges?: (diffId: string) => void;
  /**
   * Scrub cursor — null = live; number = a `sequence_no` to time-travel
   * the surface to. The host (ChatScreen) reconciles this with the
   * swimlane and mini-timeline UIs.
   */
  readonly scrubbedSeq?: number | null;
  readonly onScrub?: (sequenceNo: number) => void;
  readonly onSnapToNow?: () => void;
  /**
   * PR-3.6 right-rail slot (PRD §5 data-flow). When provided, this node is
   * rendered in the chat `gridArea` **in place of** the built-in `TcChat`
   * column — the recomposed `RunWorkspaceRail` (`[Chat · Sources · Agents ·
   * Approvals]`) mounts here, hosting `TcChat` inside its Chat tab. Rendered
   * inside the `SwimlaneScrubProvider` so a nested `TcChat` still reads the
   * scrub context. Keeping this a plain `ReactNode` lets the rail be composed
   * by the host (`RunDestination`) without `ThreadCanvas` importing
   * `WorkspacePane` (dependency-light, FR-3.11). Omitted → default `TcChat`.
   */
  readonly rightRail?: ReactNode;
  /**
   * PR-3.6: gate the in-canvas Studio/Focus switcher. Defaults to `true`
   * (standalone / web usage). `RunDestination` passes `false` so `RunHeader`
   * is the single mode control (per the PR-3.5 seam note).
   */
  readonly showModeSwitcher?: boolean;
}

const EMPTY_EVENTS: readonly RuntimeEventEnvelope[] = [];

// ============================================================
// Component
// ============================================================

export function ThreadCanvas(props: ThreadCanvasProps): ReactElement {
  const {
    mode,
    conversationId,
    runId,
    events = EMPTY_EVENTS,
    onModeChange,
    tabs,
    activeUri,
    onActivateTab,
    onCloseTab,
    transport,
    pendingDiff,
    onApprove,
    onReject,
    onSuggestChanges,
    scrubbedSeq = null,
    onScrub,
    onSnapToNow,
    rightRail,
    showModeSwitcher = true,
  } = props;

  // SINGLE projector — every consumer reads slices off this object.
  const projection = useEventProjector(events);

  // Time-travel surface payload (frozen at scrub cursor in studio/focus).
  // Auto-derived for the active uri so the surface stays consistent
  // with the swimlane/mini-timeline scrub cursor without a remount.
  const surfaceState = useMemo(() => {
    if (scrubbedSeq === null) {
      return projection.surface.payloadFor(activeUri);
    }
    // Project up to the scrub cursor — surface freezes; chat stays live.
    // We could call projectAt() directly, but going through the same
    // projector keeps single-source-of-truth. The live projection's
    // surface state is the up-to-now view; for time-travel we have to
    // re-project. This is allocator-cheap because events is stable.
    return undefined; // ChatScreen feeds `pendingDiff`+state through props
    // when scrubbed (see PRD §3.7); the surface column shows the diff
    // overlay rather than the live surface during scrub.
  }, [scrubbedSeq, projection.surface, activeUri]);

  // Forward scrub cursor to TcChat (it shows the "Viewing <time>"
  // ghost banner when off-live).
  const scrubContextValue = useMemo(
    () => ({ scrubbedTo: (scrubbedSeq ?? "now") as number | "now" }),
    [scrubbedSeq],
  );

  // Mini-timeline + tablist handlers — pure dispatch through callbacks.
  const handleScrub = (sequenceNo: number): void => {
    onScrub?.(sequenceNo);
  };
  const handleSnapToNow = (): void => {
    onSnapToNow?.();
  };
  const handleExpandToStudio = (): void => {
    onModeChange("studio");
  };

  // Layout slots — each visibility flag is a presentation toggle. The
  // JSX shape of every persistent slot is invariant across modes so
  // React reconciliation NEVER unmounts the underlying components.
  // - Studio: surface + chat + swimlanes + mini-timeline
  // - Focus:  chat (with focus-tabs view) + mini-timeline; surface is hidden
  const showSurfaceColumn = mode === "studio";
  const showSwimlanes = mode === "studio" && runId !== null;
  // Progressive disclosure (design review): when the Studio swimlanes band is
  // mounted but has zero beads, the mini-timeline would stack a SECOND empty
  // status line ("No activity yet") under the swimlanes' own "Listening for
  // run events…" — two strings, one meaning. Withhold the mini strip until the
  // first event lands; Focus (no swimlanes) keeps it always.
  const timelineEmpty = projection.timeline.beads.length === 0;
  const showMiniTimeline =
    mode === "focus" ||
    (mode === "studio" && !(showSwimlanes && timelineEmpty));
  const showTabs = mode === "studio" || mode === "focus";

  return (
    <div
      data-testid="thread-canvas"
      data-conversation-id={conversationId}
      data-mode={mode}
      data-resolved-mode={mode}
      data-has-active-surfaces={
        projection.surface.hasActiveSurfaces ? "true" : "false"
      }
      style={gridStyleFor(mode)}
    >
      {showModeSwitcher ? (
        <ModeSwitcherTabs mode={mode} onModeChange={onModeChange} />
      ) : null}

      {showTabs ? (
        <div style={tabsRowStyle}>
          <TcTabs
            tabs={tabs}
            activeUri={activeUri}
            onActivate={onActivateTab}
            onClose={onCloseTab}
          />
        </div>
      ) : null}

      <SwimlaneScrubProvider value={scrubContextValue}>
        <div
          data-testid="tc-surface-slot"
          data-visible={showSurfaceColumn ? "true" : "false"}
          style={surfaceSlotStyle(showSurfaceColumn)}
        >
          <TcSurfaceMount
            uri={activeUri}
            transport={transport}
            state={surfaceState}
            pendingDiff={pendingDiff}
            onApprove={onApprove}
            onReject={onReject}
            onSuggestChanges={onSuggestChanges}
          />
        </div>

        <div
          data-testid="tc-chat-slot"
          data-visible="true"
          data-rail={rightRail !== undefined ? "true" : "false"}
          style={chatSlotStyle(mode)}
        >
          {/* PR-3.6: when the host injects a right rail, it OWNS the chat
              column (its Chat tab hosts the single TcChat). Otherwise fall
              back to the built-in TcChat so standalone/web usage is
              unchanged. Rendered here — inside SwimlaneScrubProvider — so a
              nested TcChat still resolves the scrub context. */}
          {rightRail !== undefined ? (
            rightRail
          ) : (
            <TcChat
              conversationId={conversationId as unknown as string}
              mode={mode}
            />
          )}
        </div>

        {showSwimlanes && runId !== null ? (
          <div data-testid="tc-swimlanes-slot" style={swimlanesSlotStyle}>
            <TcSwimlanes runId={runId as unknown as string} />
          </div>
        ) : null}

        {showMiniTimeline ? (
          <div
            data-testid="tc-mini-timeline-slot"
            style={miniTimelineSlotStyle}
          >
            <TcMiniTimeline
              beads={projection.timeline.beads}
              scrubbedTo={scrubbedSeq}
              onScrub={handleScrub}
              onSnapToNow={handleSnapToNow}
              onExpand={mode === "focus" ? handleExpandToStudio : undefined}
            />
          </div>
        ) : null}
      </SwimlaneScrubProvider>
    </div>
  );
}

// ============================================================
// Mode-switcher tablist
// ============================================================
//
// Per chats-canvas-prd §9 + design-system tablist conventions, the
// mode-switcher is a `role="tablist"` of two `role="tab"` buttons
// (Studio / Focus) with `aria-pressed` reflecting the current mode.
// Autonomy is a run state, not a view — Auto mode was dropped. Arrow keys move
// focus between tabs; Enter / Space activates. No global keyboard
// chord (chat1.md L383 — buttons only).

interface ModeSwitcherTabsProps {
  readonly mode: ThreadMode;
  readonly onModeChange: (mode: ThreadMode) => void;
}

const MODE_LABELS: Record<ThreadMode, string> = {
  studio: "Studio",
  focus: "Focus",
};

function ModeSwitcherTabs(props: ModeSwitcherTabsProps): ReactElement {
  const { mode, onModeChange } = props;

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
      return;
    }
    event.preventDefault();
    const idx = MODE_VALUES.indexOf(mode);
    if (idx < 0) {
      return;
    }
    const dir = event.key === "ArrowLeft" ? -1 : 1;
    const next = (idx + dir + MODE_VALUES.length) % MODE_VALUES.length;
    onModeChange(MODE_VALUES[next]);
  };

  return (
    <div
      role="tablist"
      aria-label="Thread canvas mode"
      data-testid="tc-mode-switcher"
      style={modeSwitcherStyle}
      onKeyDown={handleKeyDown}
    >
      {MODE_VALUES.map((value) => {
        const selected = value === mode;
        const label = MODE_LABELS[value];
        return (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-pressed={selected}
            aria-label={`${label} mode`}
            tabIndex={selected ? 0 : -1}
            data-testid={`tc-mode-switcher-${value}`}
            data-mode-value={value}
            onClick={() => onModeChange(value)}
            style={modeButtonStyle(selected)}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}

// ============================================================
// Styles (tokens only)
// ============================================================

function gridStyleFor(mode: ThreadMode): CSSProperties {
  // Grid template adapts presentationally. Mode switch is a template
  // change, NOT a remount (PRD §3.3 — animation: 300ms grid-template
  // transition; the inner components are invariant).
  if (mode === "studio") {
    return {
      ...baseGridStyle,
      gridTemplateColumns: "1fr 360px",
      gridTemplateRows: "auto auto 1fr auto auto",
      gridTemplateAreas:
        '"switcher switcher" "tabs tabs" "surface chat" "swimlanes swimlanes" "mini mini"',
    };
  }
  // focus: chat-only column, surface column collapses.
  return {
    ...baseGridStyle,
    gridTemplateColumns: "minmax(0, 760px)",
    gridTemplateRows: "auto auto 1fr auto",
    gridTemplateAreas: '"switcher" "tabs" "chat" "mini"',
    justifyContent: "center",
  };
}

const baseGridStyle: CSSProperties = {
  display: "grid",
  height: "100%",
  minHeight: 0,
  width: "100%",
  background: "var(--color-bg, #0e1015)",
  color: "var(--color-text, #f4f5f6)",
  fontFamily: "var(--font-sans)",
  transition: "grid-template 300ms cubic-bezier(0.2, 0.7, 0.2, 1)",
};

const tabsRowStyle: CSSProperties = {
  gridArea: "tabs",
  minHeight: 0,
};

const surfaceSlotStyle = (visible: boolean): CSSProperties => ({
  gridArea: "surface",
  display: visible ? "flex" : "none",
  flexDirection: "column",
  minWidth: 0,
  minHeight: 0,
  borderRight: "1px solid var(--color-border, #22252e)",
  overflow: "auto",
  padding: 16,
});

const chatSlotStyle = (mode: ThreadMode): CSSProperties => ({
  gridArea: "chat",
  display: "flex",
  flexDirection: "column",
  minWidth: 0,
  minHeight: 0,
  background: "var(--color-bg-elevated, #16181f)",
  borderLeft:
    mode === "studio" ? "1px solid var(--color-border, #22252e)" : "none",
  overflow: "hidden",
});

const swimlanesSlotStyle: CSSProperties = {
  gridArea: "swimlanes",
  borderTop: "1px solid var(--color-border, #22252e)",
  background: "var(--color-bg, #0e1015)",
  minHeight: 48,
};

const miniTimelineSlotStyle: CSSProperties = {
  gridArea: "mini",
  minHeight: 32,
};

const modeSwitcherStyle: CSSProperties = {
  gridArea: "switcher",
  display: "flex",
  gap: 4,
  padding: "8px 12px",
  borderBottom: "1px solid var(--color-border, #22252e)",
  background: "var(--color-bg-elevated, #16181f)",
};

const modeButtonStyle = (selected: boolean): CSSProperties => ({
  background: selected ? "var(--color-accent)" : "transparent",
  color: selected
    ? "var(--color-accent-contrast, #101113)"
    : "var(--color-text-muted, #9aa0a6)",
  border: `1px solid ${
    selected ? "var(--color-accent)" : "var(--color-border, #2a2d31)"
  }`,
  borderRadius: 999,
  padding: "4px 12px",
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  cursor: "pointer",
  outline: "none",
  fontFamily: "inherit",
});
