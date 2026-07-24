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
  useCallback,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactElement,
  type ReactNode,
} from "react";

import type {
  ConversationId,
  RunId,
  RuntimeEventEnvelope,
} from "@0x-copilot/api-types";
import type { Transport } from "@0x-copilot/chat-transport";

import type { SurfacePayload } from "./eventProjector";
import { projectProvenance } from "./provenance";
import { projectStatusLine } from "./statusLine";
import { surfaceIdForTabUri } from "./ledgerProjection";
import { SwimlaneScrubProvider } from "./SwimlaneScrubContext";
import { TcChat } from "./TcChat";
import { TcMiniTimeline } from "./TcMiniTimeline";
import { TcStatusStrip } from "./TcStatusStrip";
import { TcSurfaceFrame } from "./TcSurfaceFrame";
import { TcSurfaceMount, type PendingDiffHandle } from "./TcSurfaceMount";
import type {
  LedgerShapeRequestState,
  LedgerSurfaceViewState,
  LedgerViewKeep,
} from "./ledgerProjection";
import { ViewTierToggle } from "./ViewTierToggle";
import { SuggestShapeButton } from "./SuggestShapeButton";
import { TcSwimlanes } from "./TcSwimlanes";
import { TcTabs, type TcTab } from "./TcTabs";
import { useEventProjector } from "./useEventProjector";

export type ThreadMode = "studio" | "focus";

const MODE_VALUES: readonly ThreadMode[] = ["studio", "focus"];

/** Studio workspace-rail (chat/tabs column) width bounds, in px. */
export const DEFAULT_RAIL_WIDTH = 360;
export const MIN_RAIL_WIDTH = 300;
export const MAX_RAIL_WIDTH = 760;
/** Minimum width kept for the surface (center) column while dragging. */
const MIN_SURFACE_WIDTH = 320;

/** Clamp a rail width to the allowed range (a non-finite value → default). */
export function clampRailWidth(width: number): number {
  if (!Number.isFinite(width)) return DEFAULT_RAIL_WIDTH;
  return Math.max(MIN_RAIL_WIDTH, Math.min(MAX_RAIL_WIDTH, Math.round(width)));
}

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
   * PRD-09c edit-on-surface slot — forwarded verbatim to `TcSurfaceMount`, which
   * mounts it as a host-owned overlay OVER the active surface's pure adapter.
   * The host (`RunDestination`) builds the `EditOverlay` and opens it from
   * `onSuggestChanges`. Omitted → no overlay (the default).
   */
  readonly editSlot?: ReactNode;
  /**
   * SURFACES_V2 (PRD-B1): when provided, the surface column resolves the active
   * surface's state through this instead of `projection.surface.payloadFor(uri)`
   * — the v2 canvas hydrates content from the SurfaceStore endpoint
   * (`useSurfacesV2`), not from v1 `payload.surface` envelopes. Only consulted
   * while live (`scrubbedSeq === null`); scrub behavior is unchanged (v2
   * time-travel is out of scope). Omitted → the v1 projection path (default).
   */
  readonly resolveSurfaceState?: (uri: string) => SurfacePayload | undefined;
  /**
   * SURFACES_V2 integration mount pass: a host-composed replacement for the
   * center surface, keyed on the active surface. Only consulted on the v2 path
   * (`resolveSurfaceState` wired) and while live (`scrubbedSeq === null`). When
   * it returns a node, that node renders IN PLACE OF the `TcSurfaceFrame` +
   * `TcSurfaceMount` + tier-toggle + status-strip block — this is how the
   * kind-specific v2 surfaces (`TcStagedDraftSurface` / `TcStagedTableSurface` /
   * `ReceiptSurface`) mount, since they render from ledger folds + host callbacks
   * rather than through the pure adapter registry. `null` ⇒ the default v2 mount
   * path (record/message/table/call/raw surfaces via the adapter). Omitted ⇒
   * byte-identical to the current v2 canvas (host never composed an override).
   */
  readonly renderSurfaceOverride?: (uri: string) => ReactNode | null;
  /**
   * SURFACES_V2 (PRD-B2): host clipboard + file-save callbacks for the raw
   * fallback's Copy / Download. Substrate-owned (the package never touches the
   * clipboard or the filesystem). Only consulted inside the v2 canvas subtree;
   * omitted → the raw fallback's buttons render disabled. Optional + default
   * absent so the flag-off path is byte-identical.
   */
  readonly onCopyText?: (text: string) => Promise<void>;
  readonly onSaveFile?: (text: string, filename: string) => Promise<void>;
  /**
   * SURFACES_V2 (PRD-B3): the active surface's folded view-lifecycle state
   * (tier ladder + preference + regen) + the two mutation callbacks. When all
   * three are provided the surface chrome renders the `ViewTierToggle`
   * (Generic ⇄ Shaped + Regenerate) beside B2's provenance footer. The
   * callbacks ride the host Transport port (RunDestination POSTs to the
   * surface-view endpoints); omitted → no toggle (flag-off byte-identical).
   */
  readonly activeViewState?: LedgerSurfaceViewState | null;
  readonly onRegenerateView?: (surfaceId: string) => void;
  readonly onSetViewPreference?: (
    surfaceId: string,
    keep: LedgerViewKeep,
  ) => void;
  /**
   * SURFACES_V2 (PRD-B4): the active surface's folded "Suggest a shape" state +
   * the invited-shaping callback. When both are provided AND the active surface's
   * effective tier is `raw`/`generic`, the surface chrome renders the
   * `SuggestShapeButton` beside the tier toggle. The callback rides the host
   * Transport port (RunDestination POSTs to the shape-request endpoint); omitted
   * → no button (flag-off byte-identical).
   */
  readonly activeShapeRequest?: LedgerShapeRequestState;
  readonly onShapeRequest?: (surfaceId: string) => void;
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
  /**
   * Width (px) of the Studio workspace rail (the chat/tabs column). Controlled
   * by the host (`RunDestination` via `useRailWidth`, KeyValueStore-backed) so
   * the width persists across sessions; defaults to `DEFAULT_RAIL_WIDTH` for
   * standalone callers. Ignored in Focus mode (single centered column).
   */
  readonly railWidth?: number;
  /**
   * Fired with the new width when the user finishes dragging (or arrow-keys) the
   * rail divider. Omit for a non-persistent, session-only resize.
   */
  readonly onRailWidthChange?: (width: number) => void;
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
    editSlot,
    resolveSurfaceState,
    renderSurfaceOverride,
    onCopyText,
    onSaveFile,
    activeViewState,
    onRegenerateView,
    onSetViewPreference,
    activeShapeRequest = "idle",
    onShapeRequest,
    scrubbedSeq = null,
    onScrub,
    onSnapToNow,
    rightRail,
    showModeSwitcher = true,
    railWidth = DEFAULT_RAIL_WIDTH,
    onRailWidthChange,
  } = props;

  // SINGLE projector — every consumer reads slices off this object.
  const projection = useEventProjector(events);

  // Studio rail resize. `dragWidth` is a transient local override held only
  // while the user drags the divider; on release we commit to the host (which
  // persists) and fall back to the controlled `railWidth`. Callers without an
  // `onRailWidthChange` keep the dragged width for the session.
  const gridRef = useRef<HTMLDivElement | null>(null);
  const [dragWidth, setDragWidth] = useState<number | null>(null);
  const draggingRef = useRef(false);
  const railWidthPx = clampRailWidth(dragWidth ?? railWidth);

  const widthFromPointer = useCallback((clientX: number): number => {
    const grid = gridRef.current;
    if (grid === null) return DEFAULT_RAIL_WIDTH;
    const rect = grid.getBoundingClientRect();
    const dynamicMax = Math.min(MAX_RAIL_WIDTH, rect.width - MIN_SURFACE_WIDTH);
    return Math.max(
      MIN_RAIL_WIDTH,
      Math.min(dynamicMax, Math.round(rect.right - clientX)),
    );
  }, []);

  const handleResizeDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>): void => {
      event.preventDefault();
      draggingRef.current = true;
      event.currentTarget.setPointerCapture(event.pointerId);
      setDragWidth(widthFromPointer(event.clientX));
    },
    [widthFromPointer],
  );

  const handleResizeMove = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>): void => {
      if (!draggingRef.current) return;
      setDragWidth(widthFromPointer(event.clientX));
    },
    [widthFromPointer],
  );

  const handleResizeUp = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>): void => {
      if (!draggingRef.current) return;
      draggingRef.current = false;
      event.currentTarget.releasePointerCapture(event.pointerId);
      const next = widthFromPointer(event.clientX);
      if (onRailWidthChange !== undefined) {
        onRailWidthChange(next);
        setDragWidth(null);
      } else {
        setDragWidth(next);
      }
    },
    [onRailWidthChange, widthFromPointer],
  );

  const handleResizeKey = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>): void => {
      // The rail is on the right, so ArrowLeft widens it (divider moves left).
      const delta =
        event.key === "ArrowLeft" ? 16 : event.key === "ArrowRight" ? -16 : 0;
      if (delta === 0) return;
      event.preventDefault();
      const next = clampRailWidth(railWidthPx + delta);
      if (onRailWidthChange !== undefined) {
        onRailWidthChange(next);
      } else {
        setDragWidth(next);
      }
    },
    [onRailWidthChange, railWidthPx],
  );

  // Time-travel surface payload (frozen at scrub cursor in studio/focus).
  // Auto-derived for the active uri so the surface stays consistent
  // with the swimlane/mini-timeline scrub cursor without a remount.
  const surfaceState = useMemo(() => {
    if (scrubbedSeq === null) {
      // SURFACES_V2 (PRD-B1): when the host wired the v2 resolver, the active
      // surface's content comes from the SurfaceStore hydration (`useSurfacesV2`)
      // — never mixed with the v1 `payload.surface` projection. `undefined`
      // (not yet hydrated) falls to TcSurfaceMount's tier-3 floor.
      if (resolveSurfaceState !== undefined) {
        return resolveSurfaceState(activeUri);
      }
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
  }, [scrubbedSeq, projection.surface, activeUri, resolveSurfaceState]);

  // SURFACES_V2 (PRD-B2): the v2 canvas is active exactly when the host wired
  // the ledger-hydration resolver (B1's signal). All B2 chrome mounts strictly
  // inside this condition, so flag-off is byte-identical.
  const surfacesV2On = resolveSurfaceState !== undefined;
  // Integration mount pass: the host-composed, kind-specific v2 surface for the
  // active tab (staged draft/table, receipt). Only consulted on the v2 path and
  // while live — scrub time-travel for these surfaces is out of scope, and the
  // scrubbed surface column already shows the diff overlay, not the live mount.
  const surfaceOverride =
    surfacesV2On && scrubbedSeq === null && renderSurfaceOverride !== undefined
      ? (renderSurfaceOverride(activeUri) ?? null)
      : null;
  // Pure PEERS of `useEventProjector` over the SAME events array (one-projector
  // invariant). Computed unconditionally (Rules of Hooks); only READ when v2 is
  // on, so the flag-off path pays nothing but the memo.
  const provenanceById = useMemo(() => projectProvenance(events), [events]);
  const statusLine = useMemo(() => projectStatusLine(events), [events]);
  const activeProvenance = useMemo(() => {
    if (!surfacesV2On) return null;
    const id = surfaceIdForTabUri(activeUri);
    return id !== null ? (provenanceById.get(id) ?? null) : null;
  }, [surfacesV2On, activeUri, provenanceById]);

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
      ref={gridRef}
      data-testid="thread-canvas"
      data-conversation-id={conversationId}
      data-mode={mode}
      data-resolved-mode={mode}
      data-has-active-surfaces={
        projection.surface.hasActiveSurfaces ? "true" : "false"
      }
      style={{
        ...gridStyleFor(mode, railWidthPx),
        // Kill the 300ms grid-template animation during an active drag so the
        // rail tracks the pointer 1:1 (the animation is for mode switches).
        ...(dragWidth !== null ? { transition: "none" } : null),
      }}
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
          {surfaceOverride !== null ? (
            <div
              data-testid="tc-surface-v2-override"
              style={surfaceOverrideStyle}
            >
              {surfaceOverride}
            </div>
          ) : surfacesV2On ? (
            <>
              <TcSurfaceFrame
                provenance={activeProvenance}
                rawPayload={surfaceState}
                onCopyText={onCopyText}
                onSaveFile={onSaveFile}
              >
                <TcSurfaceMount
                  uri={activeUri}
                  transport={transport}
                  state={surfaceState}
                  pendingDiff={pendingDiff}
                  onApprove={onApprove}
                  onReject={onReject}
                  onSuggestChanges={onSuggestChanges}
                  editSlot={editSlot}
                />
              </TcSurfaceFrame>
              {/* PRD-B3: the persistent tier toggle + Regenerate, beside the
                  provenance footer. Rendered only when the host supplies the
                  active surface's folded view state + both callbacks. */}
              {activeViewState != null &&
              onRegenerateView !== undefined &&
              onSetViewPreference !== undefined &&
              surfaceIdForTabUri(activeUri) !== null ? (
                <ViewTierToggle
                  surfaceId={surfaceIdForTabUri(activeUri) as string}
                  viewState={activeViewState}
                  onRegenerateView={onRegenerateView}
                  onSetViewPreference={onSetViewPreference}
                />
              ) : null}
              {/* PRD-B4: "Suggest a shape" on the raw/generic fallback only. A
                  shaped surface hides it (the automatic/invited upgrade already
                  landed). Rendered when the host supplies the callback. */}
              {onShapeRequest !== undefined &&
              surfaceIdForTabUri(activeUri) !== null &&
              (activeViewState == null ||
                activeViewState.effectiveTier === "raw" ||
                activeViewState.effectiveTier === "generic") ? (
                <SuggestShapeButton
                  surfaceId={surfaceIdForTabUri(activeUri) as string}
                  shapeRequest={activeShapeRequest}
                  onShapeRequest={onShapeRequest}
                />
              ) : null}
              <TcStatusStrip line={statusLine} />
            </>
          ) : (
            <TcSurfaceMount
              uri={activeUri}
              transport={transport}
              state={surfaceState}
              pendingDiff={pendingDiff}
              onApprove={onApprove}
              onReject={onReject}
              onSuggestChanges={onSuggestChanges}
              editSlot={editSlot}
            />
          )}
        </div>

        {/* Draggable divider between the surface and the rail — Studio only.
            The 1px `handle` grid column carries the line; the inner span widens
            the grab area to ~9px. Pointer capture keeps the drag alive off the
            thin target; arrow keys nudge for keyboard users. */}
        {showSurfaceColumn ? (
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize workspace rail"
            aria-valuenow={railWidthPx}
            aria-valuemin={MIN_RAIL_WIDTH}
            aria-valuemax={MAX_RAIL_WIDTH}
            tabIndex={0}
            data-testid="tc-rail-resizer"
            data-dragging={dragWidth !== null ? "true" : "false"}
            style={railHandleStyle(dragWidth !== null)}
            onPointerDown={handleResizeDown}
            onPointerMove={handleResizeMove}
            onPointerUp={handleResizeUp}
            onKeyDown={handleResizeKey}
          >
            <span style={railHandleHitStyle} aria-hidden="true" />
          </div>
        ) : null}

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

function gridStyleFor(mode: ThreadMode, railWidthPx: number): CSSProperties {
  // Grid template adapts presentationally. Mode switch is a template
  // change, NOT a remount (PRD §3.3 — animation: 300ms grid-template
  // transition; the inner components are invariant).
  if (mode === "studio") {
    // A 1px `handle` column between surface and chat carries the drag divider;
    // the rail (chat column) width is user-controlled (`railWidthPx`).
    return {
      ...baseGridStyle,
      gridTemplateColumns: `minmax(0, 1fr) 1px ${railWidthPx}px`,
      gridTemplateRows: "auto auto 1fr auto auto",
      gridTemplateAreas:
        '"switcher switcher switcher" "tabs tabs tabs" "surface handle chat" "swimlanes swimlanes swimlanes" "mini mini mini"',
    };
  }
  // focus: the surface column collapses; the `chat` area spans the full width
  // so the injected rail (RunWorkspaceRail) can lay out the design's two-column
  // split internally — Chat (730px centered) | Run-details panel (324/46px). The
  // mini-timeline stays full-width below (WS-F).
  return {
    ...baseGridStyle,
    gridTemplateColumns: "minmax(0, 1fr)",
    gridTemplateRows: "auto auto 1fr auto",
    gridTemplateAreas: '"switcher" "tabs" "chat" "mini"',
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
  // The divider is the `handle` grid column (see railHandleStyle) — no border
  // here, or it would double the line next to the handle.
  overflow: "auto",
  padding: 16,
});

// The v2 kind-specific surface override fills the surface column and scrolls its
// own content (staged tables / receipts can be long).
const surfaceOverrideStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  minWidth: 0,
  minHeight: 0,
};

const chatSlotStyle = (_mode: ThreadMode): CSSProperties => ({
  gridArea: "chat",
  display: "flex",
  flexDirection: "column",
  minWidth: 0,
  minHeight: 0,
  background: "var(--color-bg-elevated, #16181f)",
  overflow: "hidden",
});

// Studio surface|rail divider. The 1px grid column is the visible line; the
// inner hit span (below) widens the grab area. Turns accent while dragging.
const railHandleStyle = (dragging: boolean): CSSProperties => ({
  gridArea: "handle",
  position: "relative",
  alignSelf: "stretch",
  background: dragging
    ? "var(--color-accent, #5fb2ec)"
    : "var(--color-border, #22252e)",
  cursor: "col-resize",
  touchAction: "none",
  zIndex: 2,
});

const railHandleHitStyle: CSSProperties = {
  position: "absolute",
  top: 0,
  bottom: 0,
  left: -4,
  right: -4,
  cursor: "col-resize",
};

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
