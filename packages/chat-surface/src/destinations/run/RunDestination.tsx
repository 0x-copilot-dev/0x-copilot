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
// The header (`RunHeader`) shows a state-aware kicker ("ACTIVE RUN" / "STANDBY") + goal and the
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
//   - PR-3.11 empty/multi-run (DONE): `session.runs` + `session.selectRun` back
//     the `RunMultiSelect` (mounted after the header when `runs.length > 1`),
//     and `RunEmptyState` (goal composer) mounts in the canvas slot when
//     `session.runId === null`. Starting a goal binds the fresh run through the
//     `runId` seam (`startedRunId` feeds `useRunSession.runId`), so empty→live
//     swaps the slot content IN PLACE without remounting the shell (FR-3.25).
//
// Boundary: framework-agnostic. All I/O is port-only — Transport (via
// `useTransport`) + KeyValueStore (inside `useRunMode`); no bare
// window/document/fetch/localStorage (FR-3.27).

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type {
  ConversationId,
  RunId,
  SurfaceEdits,
} from "@0x-copilot/api-types";

import {
  humanTransportMessage,
  parseTransportError,
} from "../../errors/transportError";
// PRD-09c: the host-owned edit-on-surface overlay. Mounted OVER the pure adapter
// via ThreadCanvas.editSlot → TcSurfaceMount; its submit reuses resolveApproval.
import { EditOverlay } from "../../surfaces/edit/EditOverlay";
import { useTransport } from "../../providers/TransportProvider";
// PR-3.8: pure selector projecting parallel-subagent + fleet state off the
// single canonical event stream (no second subscription / projector).
import { projectSubagents } from "../../subagents";
import {
  ThreadCanvas,
  TcChat,
  projectSurfaceTabs,
  type TcTab,
  type PendingDiffHandle,
} from "../../thread-canvas";

// PR-3.10: pure selector projecting approval state off the SAME single canonical
// event stream (FR-3.3). Feeds the in-chat ApprovalCard/conf-card (TcChat) and
// the Approvals-tab count (RunWorkspaceRail); no second subscription/projector.
import {
  overlayApprovalDecisions,
  projectApprovals,
  toApprovalsQueue,
  type RunApprovalDecision,
} from "./approvalProjection";
// PR-3.11: the two prototype-gap states — the empty/idle goal composer
// (FR-3.25) and the multi-run selector (FR-3.26). Both mount inside this shell
// (no separate host remount): the empty state binds a freshly-started run via
// the `runId` seam, and the selector rebinds the session via `selectRun`.
import { RunEmptyState, type StartRunError } from "./RunEmptyState";
// PRD-04: pure selector projecting proposed surface diffs off the SAME single
// canonical event stream (FR-3.3). Feeds the on-surface Approve/Reject controls
// in TcSurfaceMount (via ThreadCanvas.pendingDiff); no second subscription.
import { projectSurfaceDiffs } from "./_surfaceDiffs";
import { RunHeader } from "./RunHeader";
import { RunMultiSelect } from "./RunMultiSelect";
import { RunWorkspaceRail } from "./RunWorkspaceRail";
import { useRailWidth } from "./useRailWidth";
import { useRunMode } from "./useRunMode";
import { useRunSources } from "./useRunSources";
import { useRunTranscript } from "./useRunTranscript";
import { useRunSession } from "./useRunSession";

const EMPTY_DECISIONS: ReadonlyMap<string, RunApprovalDecision> = new Map();
const EMPTY_CLOSED_URIS: ReadonlySet<string> = new Set();
/** Surface-tab strip cap (PRD-04 — "+N more" overflow lands later). */
const MAX_SURFACE_TABS = 8;

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
  /**
   * PR-3.11 (FR-3.25): start a run from the empty-state goal composer. The host
   * owns run creation (identity + model), returning the new `runId` (or `null`
   * on failure). When unset, the shell falls back to a default `POST
   * /v1/agent/runs` through the Transport port (identity is derived from the
   * verified session, never sent by the client). Either way the returned id is
   * bound back into `useRunSession` via the `runId` seam, so empty→live never
   * remounts the shell.
   */
  readonly onStartRun?: (
    goal: string,
  ) => Promise<string | null> | string | null;
  /**
   * Readiness gate (Issue 1): `false` when NO model provider is configured (no
   * BYOK key and no local model), so the empty-state composer shows a "Set up
   * your model" CTA and refuses to start a run that would fail with a
   * configuration error. Defaults to `true` so existing mounts/tests are
   * unaffected; the host binder computes it from the provider-keys /
   * local-models readiness probe.
   */
  readonly modelReady?: boolean;
  /**
   * Open Settings → Provider keys. Threaded to the empty-state composer for the
   * setup CTA and the `configuration_error` "Add a provider key" CTA. Host-owned
   * so the substrate-agnostic package never navigates directly.
   */
  readonly onOpenModelSettings?: () => void;
  /**
   * Composer slot override for the in-cockpit chat (`TcChat`). Forwarded
   * verbatim to `TcChat.renderComposer`, letting a host mount the full
   * `AssistantComposer` (attachments, `/`-menu, connectors, model picker) in
   * place of the bare base `Composer` while the cockpit keeps owning the
   * scrub/ghost gating (it hands the injected composer the `disabled` +
   * `placeholder` state). Omitted → the base composer renders unchanged.
   */
  readonly renderComposer?: (ctx: {
    readonly disabled: boolean;
    readonly placeholder: string;
  }) => ReactElement | null;
}

export function RunDestination(props: RunDestinationProps): ReactElement {
  const {
    conversationId,
    runId: explicitRunId = null,
    enabled = true,
    agentName,
    goal: goalOverride,
    onStartRun,
    modelReady = true,
    onOpenModelSettings,
    renderComposer,
  } = props;

  const transport = useTransport();

  // PR-3.11 (FR-3.25): the run the empty-state composer just started. It feeds
  // the SAME `runId` input the `explicitRunId` prop uses, so binding a freshly
  // created run flips the session live WITHOUT the host remounting the shell:
  // the empty state unmounts and the live layout mounts in place.
  const [startedRunId, setStartedRunId] = useState<RunId | null>(null);
  const [isStartingRun, setIsStartingRun] = useState(false);
  // The last start-run failure, surfaced in the empty-state composer so a
  // failed "Start run" is never silent (no backend, 4xx/5xx, transport error).
  // Structured (safe_message / code / correlation_id) so the composer can show
  // the actionable line + an "Add a provider key" CTA and demote the raw
  // envelope — never the wall of JSON the transport throws (Issue 2).
  const [startError, setStartError] = useState<StartRunError | null>(null);
  // The goal the empty-state composer just started the run with. Bridges the
  // header until the run list re-resolves to carry the run's own goal — so the
  // empty→live transition never flashes the idle placeholder for a run we named.
  const [startedGoal, setStartedGoal] = useState<string | null>(null);

  // A new conversation clears the last-started run so a stale id never streams
  // against it (mirrors `useRunSession`'s own per-conversation reset).
  useEffect(() => {
    setStartedRunId(null);
    setIsStartingRun(false);
    setStartedGoal(null);
    setStartError(null);
    // PRD-04: a new conversation starts from a clean surface strip.
    setPinnedUri(null);
    setClosedUris(EMPTY_CLOSED_URIS);
    // PRD-09c: never carry an open edit overlay across conversations.
    setEditingDiffId(null);
  }, [conversationId]);

  const session = useRunSession({
    conversationId,
    runId: startedRunId ?? explicitRunId,
    enabled,
  });
  const { mode, setMode } = useRunMode({ conversationId, enabled });
  // Persisted, draggable width of the Studio workspace rail (global preference).
  const { width: railWidth, setWidth: setRailWidth } = useRailWidth();

  // Surface-tab strip (PRD-04). `ThreadCanvas` takes `tabs`/`activeUri` as
  // host-controlled props; the shell DERIVES them from the single projection —
  // `projectSurfaceTabs` is a pure selector over `session.events` (the SAME
  // array ThreadCanvas hands to `useEventProjector`), NOT a second subscription
  // / projector (FR-3.3). `activeUri` auto-follows the newest surface while the
  // user hasn't pinned; a manual tab click pins (below), a pending diff pulls
  // focus, and the "follow live" affordance un-pins.
  //
  // `pinnedUri` = the tab the user manually opened (null → auto-follow live).
  // `closedUris` = tabs the user dismissed (a stale pin/close self-heals once
  // the URI leaves the projection — no per-conversation reset needed, though we
  // clear both on run switch below for a clean surface).
  const [pinnedUri, setPinnedUri] = useState<string | null>(null);
  const [closedUris, setClosedUris] =
    useState<ReadonlySet<string>>(EMPTY_CLOSED_URIS);

  // PRD-09c: which pending surface diff (by `diffId === approvalId`) currently
  // has the edit overlay open. `null` = no overlay. Opened by
  // `handleSuggestChanges` (the PRD-04 passthrough this PRD fills); the overlay
  // renders only while this matches the active pending diff, so a resolved diff
  // (optimistic or server) closes it automatically.
  const [editingDiffId, setEditingDiffId] = useState<string | null>(null);

  const handleActivateTab = useCallback((uri: string): void => {
    // A manual tab click pins — the strip stops auto-following newer surfaces
    // until the user follows live again (or the pinned surface leaves the run).
    setPinnedUri(uri);
  }, []);
  const handleCloseTab = useCallback((uri: string): void => {
    setClosedUris((prev) => {
      if (prev.has(uri)) {
        return prev;
      }
      const next = new Set(prev);
      next.add(uri);
      return next;
    });
    setPinnedUri((prev) => (prev === uri ? null : prev));
  }, []);
  const handleFollowLive = useCallback((): void => {
    setPinnedUri(null);
  }, []);

  // PR-3.7: scrub cursor + the surface tab it snaps to.
  //
  // The shell OWNS the scrub cursor (`scrubbedSeq`, a `sequence_no`; `null` =
  // live) and the "Viewing…" gating it drives. `ThreadCanvas` already plumbs
  // `scrubbedSeq`/`onScrub`/`onSnapToNow` down to `TcMiniTimeline` and the
  // `SwimlaneScrubProvider` the injected `TcChat` reads — so this is a pure
  // state lift: the mini-timeline dispatches a bead's `sequence_no` up here and
  // we reconcile the surface tab + the "Viewing…" banner + the composer/approval
  // gate. Setting a non-null cursor is what flips the cockpit off-live; the
  // composer disables + the in-chat ghost banner light up automatically because
  // `ThreadCanvas` feeds this value through `SwimlaneScrubProvider`.
  const [scrubbedSeq, setScrubbedSeq] = useState<number | null>(null);

  // PR-3.7: a cheap `sequence_no → { atMs, surfaceUri }` index over the RAW
  // session events — NOT a second `project()` call (the one projection lives in
  // ThreadCanvas, FR-3.3). It answers the two questions a scrub asks: which
  // surface did that bead touch (the `snapSet` target) and when did it happen
  // (the banner's HH:MM). Memoised on the append-only events reference.
  const scrubIndex = useMemo(() => {
    const index = new Map<number, ScrubTarget>();
    for (const event of session.events) {
      const surfaceUri = scrubUriOf(event);
      const parsed = Date.parse(event.created_at);
      index.set(event.sequence_no, {
        atMs: Number.isNaN(parsed) ? null : parsed,
        surfaceUri,
      });
    }
    return index;
  }, [session.events]);

  // PR-3.7 (FR-3.15) — `snapSet`: off-now, `activeUri` derives to the scrubbed
  // bead's surface (see the surface-tab derivation below), so scrubbing reveals
  // a past surface without mutating strip state. Setting `scrubbedSeq` is what
  // surfaces the "Viewing…" banner, hides approvals, and disables the composer.
  const handleScrub = useCallback((sequenceNo: number): void => {
    setScrubbedSeq(sequenceNo);
  }, []);

  // PR-3.7 (FR-3.16) — snap-to-now: clear the cursor. That alone clears the
  // "Viewing…" banner and re-enables the composer + approvals (both read the
  // cursor). Invoked by the banner's "Return to live →" and by the timeline's
  // ⌘L / Escape (via `ThreadCanvas.onSnapToNow`).
  const handleSnapToNow = useCallback((): void => {
    setScrubbedSeq(null);
  }, []);

  // PR-3.7: the moment being viewed, for the banner label (null when live or
  // when the scrubbed event carried no parseable timestamp).
  const viewingAtMs =
    scrubbedSeq !== null ? (scrubIndex.get(scrubbedSeq)?.atMs ?? null) : null;
  const isScrubbed = scrubbedSeq !== null;

  // PR-3.11 (FR-3.25): start a run from the empty-state goal composer. The host
  // `onStartRun` wins (it owns identity/model); otherwise the shell POSTs a run
  // through the Transport port — identity is derived from the verified session,
  // so the client sends only the conversation + the goal. The returned id is
  // bound via `setStartedRunId`, which feeds the `runId` seam and flips the
  // cockpit live in place (no shell remount).
  const { selectRun } = session;
  const handleStartGoal = useCallback(
    (goal: string): void => {
      const trimmed = goal.trim();
      // Readiness gate (Issue 1): never fire a start that is guaranteed to fail
      // with a configuration error. The composer disables itself when
      // `modelReady` is false; this guards the keyboard path too.
      if (trimmed === "" || isStartingRun || !modelReady) {
        return;
      }
      setIsStartingRun(true);
      setStartError(null);
      setStartedGoal(trimmed);
      const start = onStartRun
        ? Promise.resolve(onStartRun(trimmed))
        : transport
            .request<unknown>({
              method: "POST",
              path: "/v1/agent/runs",
              body: { conversation_id: conversationId, user_input: trimmed },
            })
            .then((payload) => runIdFromCreateResponse(payload));
      void start
        .then((newRunId) => {
          if (newRunId !== null && newRunId !== undefined && newRunId !== "") {
            setStartedRunId(newRunId as RunId);
          } else {
            // The POST resolved but carried no run id — surface it rather than
            // sitting on the composer with no feedback.
            setStartError({
              message:
                "Couldn't start the run — the agent service didn't return a run. Is the backend running?",
            });
          }
        })
        .catch((err: unknown) => {
          // Never swallow, and never dump the raw transport envelope: parse out
          // the actionable `safe_message` + `code` so the composer shows the
          // one useful line (e.g. "Missing API key…") and a CTA, with the raw
          // detail demoted behind "Show details" (Issue 2).
          const parsed = parseTransportError(err);
          setStartError({
            message:
              parsed.safeMessage ??
              "Couldn't start the run. Is the backend running and a model configured?",
            code: parsed.code,
            correlationId: parsed.correlationId,
            raw: parsed.raw !== "" ? parsed.raw : undefined,
          });
        })
        .finally(() => {
          setIsStartingRun(false);
        });
    },
    [conversationId, isStartingRun, modelReady, onStartRun, transport],
  );

  // PR-3.11 (FR-3.26): bind the cockpit to another run. `selectRun` wins over
  // the started/explicit run in `useRunSession`, so the event projector, tabs,
  // timeline, and surface all rebind to the picked run's own state; the shell
  // also resets scrub + the surface-tab strip so mode/scrub reset appropriately.
  const handleSelectRun = useCallback(
    (nextRunId: string): void => {
      setScrubbedSeq(null);
      setPinnedUri(null);
      setClosedUris(EMPTY_CLOSED_URIS);
      // PRD-09c: rebinding the cockpit to another run closes any open overlay.
      setEditingDiffId(null);
      selectRun(nextRunId);
    },
    [selectRun],
  );

  // Goal: explicit override wins, else the selected run's list entry, else —
  // for a freshly started run not yet in the list — the goal we started it with
  // (PR-3.11), so the empty→live header never regresses to the idle placeholder.
  const derivedGoal = useMemo(() => {
    if (goalOverride !== undefined) {
      return goalOverride;
    }
    const listed =
      session.runs.find((run) => run.runId === session.runId)?.goal ?? null;
    if (listed !== null) {
      return listed;
    }
    if (session.runId !== null && session.runId === startedRunId) {
      return startedGoal;
    }
    // A run IS attached but carries no goal text (explicit runId binding, or
    // a list entry without a goal). The header must still claim the run —
    // "STANDBY" over a subscribed run is a lie (design review) — so fall back
    // to an honest generic title rather than null (null → idle copy).
    if (session.runId !== null) {
      return "Untitled run";
    }
    return null;
  }, [goalOverride, session.runs, session.runId, startedRunId, startedGoal]);

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

  // The chat transcript: persisted history ⊕ the live streamed reply, projected
  // off the SAME single event stream (FR-3.3). This binder closes the streaming
  // gap — previously `projection.chat` was computed and dropped and TcChat
  // rendered a stale one-time GET. TcChat now renders exactly `messages`, so the
  // streamed reply appears live in BOTH Studio and Focus, no second fetch.
  const { messages: transcriptMessages } = useRunTranscript({
    conversationId: conversationId as unknown as string,
    runId: session.runId,
    runStatus: session.runStatus,
    events: session.events,
  });

  // The Sources tab: persisted citations (GET /sources) ⊕ the live
  // `source_ingested`/`sources_ingested` events off the SAME stream (FR-3.3) —
  // mirrors the transcript binder. Without this the rail fell back to
  // EMPTY_SOURCES, so the Sources tab was always empty despite a working
  // backend citation pipeline.
  const {
    sources,
    loading: sourcesLoading,
    error: sourcesError,
  } = useRunSources({
    conversationId: conversationId as unknown as string,
    runId: session.runId,
    runStatus: session.runStatus,
    events: session.events,
  });

  // PR-3.10: the approval queue is projected off the SAME `session.events`
  // (FR-3.3 — no second subscription/projector). `localDecisions` overlays the
  // user's optimistic Approve/Reject so the in-chat card flips to its receipt
  // immediately, before the trailing `approval_resolved` SSE frame lands; the
  // server projection then reconciles it (a server-resolved approval always
  // wins). The two approval consumers — TcChat (card/conf-card) and the rail
  // (Approvals tab + count) — both read this ONE projection.
  const [localDecisions, setLocalDecisions] =
    useState<ReadonlyMap<string, RunApprovalDecision>>(EMPTY_DECISIONS);

  const approvalProjection = useMemo(
    () =>
      overlayApprovalDecisions(
        projectApprovals(session.events),
        localDecisions,
      ),
    [session.events, localDecisions],
  );

  // PR-3.10 (FR-3.15): approvals are HIDDEN while scrubbed off-now — you cannot
  // approve a past state. Snap-to-now (`scrubbedSeq === null`) restores them.
  const chatApprovals = isScrubbed ? [] : approvalProjection.approvals;
  const approvalsQueue = useMemo(
    () => (isScrubbed ? undefined : toApprovalsQueue(approvalProjection)),
    [isScrubbed, approvalProjection],
  );

  // PR-3.10: resolve an approval. The UI is optimistically resolved via
  // `localDecisions`; the host owns the POST (D28), fired best-effort through
  // the Transport port — a failure leaves the optimistic state (the trailing
  // SSE frame is the authority) rather than blocking the cockpit.
  const resolveApproval = useCallback(
    (
      approvalId: string,
      decision: RunApprovalDecision,
      edits?: SurfaceEdits,
    ): void => {
      // Optimistic overlay uses the terminal decision ("approved"/"rejected");
      // `approve_with_edits` resolves to `approved` server-side (api-types §PRD-09a),
      // so an edited approval clears the diff the same way a plain approve does.
      setLocalDecisions((prev) => {
        if (prev.get(approvalId) === decision) {
          return prev;
        }
        const next = new Map(prev);
        next.set(approvalId, decision);
        return next;
      });
      // The wire decision carries the reviewer's edits when present; the server
      // (ai-backend 09b) re-derives final = proposal ⊕ edits and never trusts a
      // client-sent merged artifact. Plain approve/reject is unchanged.
      const body =
        edits !== undefined
          ? { decision: "approve_with_edits", edits }
          : { decision };
      void transport
        .request({
          method: "POST",
          path: `/v1/agent/approvals/${approvalId}/decision`,
          body,
        })
        .catch(() => {
          /* optimistic: SSE `approval_resolved` reconciles the truth */
        });
    },
    [transport],
  );

  const handleApprove = useCallback(
    (approvalId: string): void => resolveApproval(approvalId, "approved"),
    [resolveApproval],
  );
  const handleReject = useCallback(
    (approvalId: string): void => resolveApproval(approvalId, "rejected"),
    [resolveApproval],
  );
  // PRD-09c: open the edit overlay for the surface whose diff the reviewer wants
  // to change. This fills the PRD-04 passthrough — the overlay renders OVER the
  // active surface (ThreadCanvas.editSlot) and submits `approve_with_edits`.
  const handleSuggestChanges = useCallback((diffId: string): void => {
    setEditingDiffId(diffId);
  }, []);
  // PRD-09c: commit the reviewer's edits — reuses the SAME resolveApproval POST
  // machinery the plain approve/reject path uses, with the `approve_with_edits`
  // decision + `edits` payload. Optimistically clears the diff (as `approved`);
  // the trailing `approval_resolved` SSE frame reconciles the truth.
  const handleSubmitEdits = useCallback(
    (diffId: string, edits: SurfaceEdits): void => {
      resolveApproval(diffId, "approved", edits);
      setEditingDiffId(null);
    },
    [resolveApproval],
  );
  // PRD-09c: dismiss the overlay without committing — the pending diff (and its
  // on-surface Approve/Reject/Suggest controls) returns unchanged. No POST.
  const handleCancelEdits = useCallback((): void => {
    setEditingDiffId(null);
  }, []);

  // PRD-04: proposed surface diffs, projected off the SAME `session.events`
  // (FR-3.3 — no second subscription/projector). The SAME optimistic overlay the
  // in-chat approvals use (`diffId === approvalId`) clears a just-decided diff
  // before the trailing `approval_resolved` SSE frame lands.
  const surfaceDiffProjection = useMemo(
    () => projectSurfaceDiffs(session.events),
    [session.events],
  );
  const openSurfaceDiffs = useMemo(
    () =>
      surfaceDiffProjection.diffs.filter(
        (entry) => !localDecisions.has(entry.diffId),
      ),
    [surfaceDiffProjection, localDecisions],
  );

  // PRD-04: the surface-tab strip, derived from the single projection
  // (`projectSurfaceTabs` — pure selector over the SAME array). Cap at
  // MAX_SURFACE_TABS ("+N more" overflow lands later); drop dismissed tabs;
  // newest mutation is first.
  const surfaceTabList = useMemo(
    () => projectSurfaceTabs(session.events),
    [session.events],
  );
  const visibleSurfaceTabs = useMemo(
    () =>
      surfaceTabList
        .filter((tab) => !closedUris.has(tab.uri))
        .slice(0, MAX_SURFACE_TABS),
    [surfaceTabList, closedUris],
  );
  const newestUri =
    visibleSurfaceTabs.length > 0 ? visibleSurfaceTabs[0].uri : "";

  // `activeUri` derivation (scrub wins → pin wins → a pending diff pulls focus →
  // else follow the newest surface). A pin only holds while its surface is still
  // on the strip, so run/conversation switches self-heal.
  const effectivePin =
    pinnedUri !== null &&
    visibleSurfaceTabs.some((tab) => tab.uri === pinnedUri)
      ? pinnedUri
      : null;
  const followDiffUri =
    !isScrubbed && openSurfaceDiffs.length > 0
      ? openSurfaceDiffs[0].uri
      : undefined;
  const scrubTargetUri =
    scrubbedSeq !== null ? scrubIndex.get(scrubbedSeq)?.surfaceUri : undefined;
  const activeUri =
    isScrubbed && scrubTargetUri !== undefined && scrubTargetUri !== ""
      ? scrubTargetUri
      : (effectivePin ?? followDiffUri ?? newestUri);

  const surfaceTabs = useMemo<readonly TcTab[]>(
    () =>
      visibleSurfaceTabs.map((tab) => ({
        uri: tab.uri,
        title: tab.title ?? tab.uri,
        pinned: tab.uri === effectivePin,
      })),
    [visibleSurfaceTabs, effectivePin],
  );

  // The pending diff handed to the center pane — ONLY for the active surface,
  // and never while scrubbed off-now (FR-3.15). It clears prop-driven: once the
  // diff resolves (optimistic or server), it drops out of `openSurfaceDiffs`, so
  // TcSurfaceMount receives `null` and hides the controls (no internal state).
  const activeSurfaceDiff = isScrubbed
    ? undefined
    : openSurfaceDiffs.find((entry) => entry.uri === activeUri);
  const pendingDiff = useMemo<PendingDiffHandle | null>(
    () =>
      activeSurfaceDiff === undefined
        ? null
        : {
            diff: activeSurfaceDiff.diff,
            meta: {
              diffId: activeSurfaceDiff.diffId,
              provenance: activeSurfaceDiff.provenance,
              title: activeSurfaceDiff.title,
              regionAnchorId: activeSurfaceDiff.uri,
            },
          },
    [activeSurfaceDiff],
  );

  // PRD-09c: the edit overlay for the active surface — mounted OVER the pure
  // adapter via ThreadCanvas.editSlot → TcSurfaceMount. Renders ONLY while the
  // reviewer is editing THIS surface's diff (`editingDiffId === diffId`), so it
  // closes automatically once the diff resolves (it drops out of
  // `activeSurfaceDiff`) or the user scrubs off-now. The archetype is the uri
  // scheme (`message://…` → "message", `record://…` → "record"); v1 edits
  // message body + record fields (EditOverlay guards other archetypes).
  const editSlot = useMemo<ReactNode>(() => {
    if (
      activeSurfaceDiff === undefined ||
      editingDiffId === null ||
      editingDiffId !== activeSurfaceDiff.diffId
    ) {
      return null;
    }
    const diffId = activeSurfaceDiff.diffId;
    return (
      <EditOverlay
        archetype={schemeOf(activeSurfaceDiff.uri)}
        diff={activeSurfaceDiff.diff}
        title={activeSurfaceDiff.title}
        onSubmit={(edits) => handleSubmitEdits(diffId, edits)}
        onCancel={handleCancelEdits}
      />
    );
  }, [activeSurfaceDiff, editingDiffId, handleSubmitEdits, handleCancelEdits]);

  // PRD-04: "follow live" affordance — shown only when pinned to a surface that
  // is not the newest (reuses the scrub-banner copy pattern). Un-pins on click.
  const showFollowLive =
    !isScrubbed &&
    effectivePin !== null &&
    newestUri !== "" &&
    effectivePin !== newestUri;
  const pinnedTabTitle =
    visibleSurfaceTabs.find((tab) => tab.uri === effectivePin)?.title ?? "";

  const chatSlot = (
    <TcChat
      conversationId={conversationId as unknown as string}
      mode={mode}
      messages={transcriptMessages}
      fleets={subagentProjection.fleets}
      // PR-3.10: in-chat ApprovalCard (Studio) / conf-card (Focus) + receipts.
      approvals={chatApprovals}
      onApprove={handleApprove}
      onReject={handleReject}
      // Host composer seam: desktop mounts the full AssistantComposer here.
      renderComposer={renderComposer}
    />
  );
  // PR-3.7 (FR-3.15/3.16): while scrubbed off-now, `scrubbed` tells the rail to
  // suppress the Approvals tab — you cannot approve a past state; snap-to-now
  // restores it. PR-3.8: `subagents` feeds the Agents-tab "N live" count from
  // the single projection. PR-3.10: `approvalsQueue` feeds the Approvals-tab
  // pending count from the same projection.
  const rightRail = (
    <RunWorkspaceRail
      mode={mode}
      chatSlot={chatSlot}
      subagents={subagentProjection.subagents}
      sources={sources}
      sourcesLoading={sourcesLoading}
      sourcesError={sourcesError}
      approvalsQueue={approvalsQueue}
      onApprove={handleApprove}
      onReject={handleReject}
      scrubbed={isScrubbed}
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

      {/* PR-3.11 (FR-3.26): the multi-run selector. It renders NOTHING for a
          conversation with ≤1 run (single/zero-run cockpit stays chrome-free);
          with >1 run it lets the user rebind the whole cockpit to another run
          via `handleSelectRun` → `useRunSession.selectRun`. */}
      <RunMultiSelect
        runs={session.runs}
        selectedRunId={session.runId}
        onSelectRun={handleSelectRun}
      />

      {session.error !== null ? (
        <RunErrorBanner
          // A streamed run/resolution failure surfaces its safe_message when it
          // carries an envelope, else a cleaned line — NEVER the raw IPC string
          // (which on desktop names the remote method 'transport.request'),
          // so the banner is honest too (Issue 2 / NFR-2.1).
          message={humanTransportMessage(session.error.message)}
          onRetry={session.retry}
        />
      ) : null}

      {/* PR-3.7 (FR-3.15): off-now time-travel banner. It names the moment
          being viewed and its "Return to live →" is the snap-to-now affordance
          (FR-3.16). Complements the in-chat ghost banner (which dims the
          transcript + disables the composer via the SwimlaneScrubProvider that
          ThreadCanvas already threads from `scrubbedSeq`). */}
      {isScrubbed ? (
        <RunViewingBanner atMs={viewingAtMs} onReturnToLive={handleSnapToNow} />
      ) : null}

      {/* PRD-04: "follow live" affordance — the user pinned an older surface tab
          while the run moved on to a newer one. Reuses the scrub-banner pattern;
          "Follow live →" un-pins and resumes auto-follow. */}
      {showFollowLive ? (
        <RunFollowLiveBanner
          pinnedTitle={pinnedTabTitle}
          onFollowLive={handleFollowLive}
        />
      ) : null}

      <div data-testid="run-canvas-slot" style={canvasSlotStyle}>
        {/* PR-3.11 (FR-3.25): no active run → the empty/idle goal composer
            (never a blank ThreadCanvas / placeholder string). Submitting a goal
            starts a run and binds it via the `runId` seam (`handleStartGoal` →
            `setStartedRunId`), so the live layout below mounts IN PLACE — the
            shell (this outer div + header) never remounts. */}
        {session.runId === null ? (
          <RunEmptyState
            agentName={agentName}
            onSubmitGoal={handleStartGoal}
            submitting={isStartingRun}
            error={startError}
            setupRequired={!modelReady}
            onOpenModelSettings={onOpenModelSettings}
          />
        ) : (
          <ThreadCanvas
            mode={mode}
            conversationId={conversationId}
            runId={(session.runId as RunId | null) ?? null}
            events={session.events}
            onModeChange={setMode}
            tabs={surfaceTabs}
            activeUri={activeUri}
            onActivateTab={handleActivateTab}
            onCloseTab={handleCloseTab}
            transport={transport}
            // PRD-04: the proposed surface diff for the active surface + the
            // decision callbacks. ThreadCanvas forwards these to TcSurfaceMount,
            // which renders the Approve/Reject/Suggest controls around the diff.
            // onApprove/onReject reuse the SAME resolveApproval machinery the
            // in-chat ApprovalCard uses (diffId === approvalId); onSuggestChanges
            // is a no-op passthrough until PRD-09.
            pendingDiff={pendingDiff}
            onApprove={handleApprove}
            onReject={handleReject}
            onSuggestChanges={handleSuggestChanges}
            // PRD-09c: the host-owned edit overlay for the active surface diff.
            // Null unless the reviewer opened "Suggest changes"; when set it
            // mounts OVER the pure adapter and submits `approve_with_edits`.
            editSlot={editSlot}
            // PR-3.7: own the scrub cursor here; ThreadCanvas forwards it to the
            // mini-timeline (highlight + step/snap dispatch) and to the
            // SwimlaneScrubProvider (in-chat ghost banner + composer disable).
            scrubbedSeq={scrubbedSeq}
            onScrub={handleScrub}
            onSnapToNow={handleSnapToNow}
            // PR-3.6: mount the recomposed rail in the chat column, and collapse
            // the canvas's own mode switcher so RunHeader is the single mode
            // control (per the PR-3.5 seam note).
            rightRail={rightRail}
            showModeSwitcher={false}
            // Draggable, persisted Studio rail width (useRailWidth → KV).
            railWidth={railWidth}
            onRailWidthChange={setRailWidth}
          />
        )}
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
// PR-3.7 — time-travel ("Viewing…") banner + scrub helpers
// ============================================================
//
// Source: PRD FR-3.15 / FR-3.16 + §9 ("Scrubbed" checklist). When the cockpit
// is scrubbed off-now, this `role="status"` strip names the moment being
// viewed and offers the single way back to live. "Return to live →" invokes
// snap-to-now, which clears the cursor and re-enables the composer + approvals
// (both derive their disabled/hidden state from `scrubbedSeq`).

/** What a scrubbed `sequence_no` resolves to (banner time + snap target). */
interface ScrubTarget {
  readonly atMs: number | null;
  readonly surfaceUri: string | undefined;
}

/**
 * Read the surface uri an event touched, for the scrub index (`snapSet`
 * target). Accepts both the legacy flat `payload.surface_uri` and the PRD-01
 * `payload.surface.surface_uri` envelope so scrubbing snaps to the right surface
 * regardless of wire shape.
 */
// PRD-09c: the surface archetype is the uri scheme — `message://server/tool/id`
// → "message". Used to pick the EditOverlay's per-archetype form.
function schemeOf(uri: string): string {
  const idx = uri.indexOf("://");
  return idx > 0 ? uri.slice(0, idx) : "";
}

function scrubUriOf(event: {
  readonly payload?: Record<string, unknown>;
}): string | undefined {
  const flat = event.payload?.["surface_uri"];
  if (typeof flat === "string") {
    return flat;
  }
  const surface = event.payload?.["surface"];
  if (surface !== null && typeof surface === "object") {
    const nested = (surface as Record<string, unknown>)["surface_uri"];
    if (typeof nested === "string") {
      return nested;
    }
  }
  return undefined;
}

// PR-3.11 (FR-3.25): pull the new run id out of a `POST /v1/agent/runs`
// response. Tolerant of the shapes the runtime returns — a bare `{ run_id }` /
// `{ runId }` / `{ id }`, or those nested under a `run` envelope — so the
// empty→live start does not pin one exact server contract this phase.
function runIdFromCreateResponse(payload: unknown): string | null {
  const record = payload as Record<string, unknown> | null;
  if (record === null || typeof record !== "object") {
    return null;
  }
  const direct = record.run_id ?? record.runId ?? record.id;
  if (typeof direct === "string" && direct !== "") {
    return direct;
  }
  const nested = record.run as Record<string, unknown> | undefined;
  if (nested !== undefined && nested !== null && typeof nested === "object") {
    const inner = nested.run_id ?? nested.runId ?? nested.id;
    if (typeof inner === "string" && inner !== "") {
      return inner;
    }
  }
  return null;
}

/** Format the viewed moment as `HH:MM` (24h); generic when there is no time. */
function formatViewingTime(atMs: number | null): string {
  if (atMs === null) {
    return "an earlier step";
  }
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(atMs));
}

interface RunViewingBannerProps {
  readonly atMs: number | null;
  readonly onReturnToLive: () => void;
}

function RunViewingBanner(props: RunViewingBannerProps): ReactElement {
  const { atMs, onReturnToLive } = props;
  return (
    <div
      role="status"
      data-testid="run-viewing-banner"
      style={viewingBannerStyle}
    >
      <span data-testid="run-viewing-label" style={viewingTextStyle}>
        Viewing {formatViewingTime(atMs)} · the run has moved on
      </span>
      <button
        type="button"
        data-testid="run-return-to-live"
        onClick={onReturnToLive}
        style={returnToLiveButtonStyle}
      >
        Return to live →
      </button>
    </div>
  );
}

// ============================================================
// PRD-04 — "follow live" affordance (pinned-tab escape hatch)
// ============================================================
//
// When the user pins an older surface tab (a manual click) and the run moves on
// to a newer surface, this `role="status"` strip offers the single way back to
// auto-follow. It reuses the scrub-banner copy pattern (accent-soft fill,
// "Follow live →") — distinct testids so it never collides with the scrub
// banner (they are mutually exclusive: follow-live is gated to live/off-scrub).

interface RunFollowLiveBannerProps {
  readonly pinnedTitle: string;
  readonly onFollowLive: () => void;
}

function RunFollowLiveBanner(props: RunFollowLiveBannerProps): ReactElement {
  const { pinnedTitle, onFollowLive } = props;
  return (
    <div
      role="status"
      data-testid="run-follow-live-banner"
      style={viewingBannerStyle}
    >
      <span data-testid="run-follow-live-label" style={viewingTextStyle}>
        Pinned to {pinnedTitle || "a surface"} · the run has moved on
      </span>
      <button
        type="button"
        data-testid="run-follow-live"
        onClick={onFollowLive}
        style={returnToLiveButtonStyle}
      >
        Follow live →
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

// PR-3.7 — "Viewing…" banner (sky accent; jade=live/success, ember=danger — no
// lime). Accent-soft fill + accent bottom border mark the whole cockpit as
// off-live without competing with the danger-toned error banner above.
const viewingBannerStyle: CSSProperties = {
  flexShrink: 0,
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  padding: "8px 16px",
  background: "var(--color-accent-soft, rgba(95,178,236,.12))",
  borderBottom: "1px solid var(--color-accent, #5fb2ec)",
  color: "var(--color-text, #f4f5f6)",
  fontSize: "var(--font-size-xs, 12px)",
};

const viewingTextStyle: CSSProperties = {
  minWidth: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  color: "var(--color-accent, #5fb2ec)",
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: 0.4,
};

const returnToLiveButtonStyle: CSSProperties = {
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
