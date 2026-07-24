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
  useRef,
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type {
  AgentRunStatus,
  ConversationConnectorScopes,
  ConversationId,
  ModelSelectionRequest,
  RunAttachmentRequest,
  RunId,
  SourceEntry,
  SurfaceEdits,
} from "@0x-copilot/api-types";

import {
  humanTransportMessage,
  parseTransportError,
} from "../../errors/transportError";
// WC-P6a (AD-11): the run-scoped citation registry provider + the pure projection
// that feeds it. `projectCitations` is a peer of `projectSubagents` /
// `projectApprovals` — a pure selector over the SAME `session.events`, no second
// SSE subscription / projector (FR-3.3). The provider is mounted around the single
// TcChat so the host-supplied chip renderer (`markdownComponents`) resolves
// `[[N]]` / `[c<id>]` chips against it; the chip node + nav stay host-owned.
import { CitationsProvider } from "../../citations/CitationsContext";
import type { MarkdownTextProps } from "../../messages/MarkdownText";
import { projectCitations } from "./projectCitations";
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
  TcGateCard,
  TcStagedDraftSurface,
  TcStagedTableSurface,
  ViewUpgradeToast,
  projectSurfaceTabs,
  projectToolCalls,
  projectLedger,
  ledgerTabsAsSurfaceTabs,
  surfaceIdForTabUri,
  tabUriForSurface,
  type TcTab,
  type PendingDiffHandle,
  type LedgerGateWritePolicy,
  type LedgerStagedWrite,
  type LedgerViewTier,
  type LedgerShapeRequestState,
} from "../../thread-canvas";
// PRD-C2/D1/D3/E1/E2 — the Generative Surfaces v2 canvas mount pieces. All are
// pure presentational components + pure ledger folds + one Transport-fed fetch;
// the cockpit composes them behind the `surfacesV2` flag (flag off ⇒ never
// constructed, so the cockpit is byte-identical to today).
import { ReceiptSurface } from "../../surfaces/receipt";
import { PostureChip } from "./PostureChip";
import { PendingCounterChip } from "./PendingCounterChip";
import { usePendingWork } from "./usePendingWork";
import {
  projectPendingCards,
  type PendingCard,
} from "./pendingCardsProjection";
import { projectReceipt, type ReceiptProjection } from "./projectReceipt";
import {
  projectLedgerSources,
  type LedgerSourcesProjection,
} from "./projectLedgerSources";
import type { PendingAgentRow } from "@0x-copilot/api-types";
// PRD-B1: Generative Surfaces v2 content hydration (SurfaceStore endpoint via
// the Transport port). Called unconditionally (Rules of Hooks) but inert when
// `surfacesV2` is false (`enabled: false` ⇒ no request).
import { useSurfacesV2 } from "./useSurfacesV2";

// PR-3.10: pure selector projecting approval state off the SAME single canonical
// event stream (FR-3.3). Feeds the in-chat ApprovalCard/conf-card (TcChat) and
// the Approvals-tab count (RunWorkspaceRail); no second subscription/projector.
import {
  overlayApprovalDecisions,
  projectApprovals,
  toApprovalsQueue,
  type RunApprovalDecision,
} from "./approvalProjection";
// WC-P5a (AD-6/AD-7): the host-supplied MCP-OAuth launcher port TYPE. Threaded
// through to `TcChat` so the in-chat `mcp_auth` Connect card starts OAuth via the
// host (redirect/stash/callback stay host-owned, P5b) instead of the `/decision`
// POST. Optional — hosts that have not wired a launcher pass nothing and the card
// degrades to an inert (but visible) gate.
import type { McpAuthPort } from "./mcpAuthPort";
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
import type { SourceRowSlot } from "../../workspace";
import { useRailWidth } from "./useRailWidth";
import { useRunMode } from "./useRunMode";
import { useRunSources } from "./useRunSources";
import { useRunTranscript } from "./useRunTranscript";
import { useRunSession } from "./useRunSession";

const EMPTY_DECISIONS: ReadonlyMap<string, RunApprovalDecision> = new Map();
const EMPTY_CLOSED_URIS: ReadonlySet<string> = new Set();
// Generative Surfaces v2 mount-pass empties (flag-off = referentially stable so
// the memos/props never churn when the cockpit is byte-identical to today).
const EMPTY_CARDS: readonly PendingCard[] = [];
const EMPTY_RECEIPT: ReceiptProjection = { receipt: null, emittedSeq: null };
const EMPTY_GATE_POLICIES: ReadonlyMap<string, LedgerGateWritePolicy> =
  new Map();

/**
 * Best-effort extraction of a staged draft's body text from the hydrated
 * SurfaceStore payload (`useSurfacesV2.stateFor`). A message-archetype draft
 * carries its body under `data.body` / `.text` / `.content`; a bare string
 * payload IS the body. Returns `""` when nothing is hydrated yet — the staged
 * surface then renders an empty body while its approve bar still works.
 */
function draftBodyText(payload: unknown): string {
  if (payload === null || payload === undefined) return "";
  if (typeof payload === "string") return payload;
  if (typeof payload !== "object") return "";
  const record = payload as Record<string, unknown>;
  const data = record.data ?? record;
  if (typeof data === "string") return data;
  if (data !== null && typeof data === "object") {
    const d = data as Record<string, unknown>;
    for (const key of ["body", "text", "content", "message", "body_text"]) {
      const value = d[key];
      if (typeof value === "string") return value;
    }
  }
  return "";
}

// WC-P3 (AD-4): a run is still cancellable in these non-terminal states — the
// in-chat composer shows Stop instead of send. `cancelling` is already
// stopping, so it is excluded from the Stop-visible set (the button hides the
// moment cancel is in flight). Mirrors useRunTranscript/useRunSources'
// ACTIVE_RUN_STATUSES (kept local to avoid coupling on an internal const).
const CANCELLABLE_RUN_STATUSES: ReadonlySet<AgentRunStatus> = new Set([
  "queued",
  "running",
  "waiting_for_approval",
]);
/** Surface-tab strip cap (PRD-04 — "+N more" overflow lands later). */
const MAX_SURFACE_TABS = 8;

/**
 * The full payload the empty-state composer starts a run with. `goal` is the
 * user_input; the rest are the design's rich-composer selections (model pill,
 * attachments, Tools popover). A bare `{ goal }` — what the plain fallback
 * composer sends — keeps the historical "conversation + goal only" body, so a
 * host that never surfaces the rich composer is byte-unchanged. The host binder
 * (`onStartRun`) maps this to the `POST /v1/agent/runs` body; identity is always
 * derived server-side from the verified session, never sent by the client.
 */
export interface RunStartRequest {
  readonly goal: string;
  /** Resolved model selection (model pill). Omitted → runtime default. */
  readonly model?: ModelSelectionRequest | null;
  /** Composer attachments already mapped to the run-create wire shape. */
  readonly attachments?: readonly RunAttachmentRequest[];
  /**
   * Per-run web-search toggle (Tools popover). Omitted → runtime default (on);
   * an explicit `false` drops the built-in web_search tool for this run.
   */
  readonly webSearchEnabled?: boolean;
  /** Active connector scopes (Tools popover) → `request_context`. */
  readonly connectorScopes?: ConversationConnectorScopes;
}

/**
 * Context handed to the host-injected empty-state composer slot
 * (`renderEmptyComposer`). The host mounts the design's "What should we run
 * first?" rich composer (hero + starter chips + AssistantComposer) bound to its
 * substrate ports, and calls `onStartRun` with the full selection on send. The
 * cockpit keeps owning the empty→live transition: `onStartRun` binds the fresh
 * run via the `runId` seam, so the composer swaps for the live layout WITHOUT a
 * shell remount (FR-3.25). Submitting/error/readiness are cockpit-owned and
 * forwarded here so the composer reflects them (disable, inline error, setup).
 */
export interface RunEmptyComposerCtx {
  /** Start a run from the composer selection; binds the fresh run live. */
  readonly onStartRun: (request: RunStartRequest) => void;
  /** `true` while the run POST is in flight (disable the composer/send). */
  readonly submitting: boolean;
  /** Last start failure (safe_message + code), surfaced inline; `null` = none. */
  readonly startError: StartRunError | null;
  /** Clear the inline error (dismiss / next successful send). */
  readonly dismissError: () => void;
  /** `false` when no model provider is configured (BYOK key nor local model). */
  readonly modelReady: boolean;
  /** Open Settings → Provider keys (setup / configuration_error CTA). */
  readonly onOpenModelSettings?: () => void;
}

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
   * PR-3.11 (FR-3.25): start a run from the empty-state composer. The host owns
   * run creation (identity + model), returning the new `runId` (or `null` on
   * failure). It receives the full {@link RunStartRequest} — a bare `{ goal }`
   * from the plain fallback composer, or the rich selection (model, attachments,
   * Tools) from the design composer (`renderEmptyComposer`). When unset, the
   * shell falls back to a default `POST /v1/agent/runs` through the Transport
   * port (identity is derived from the verified session, never sent by the
   * client). Either way the returned id is bound back into `useRunSession` via
   * the `runId` seam, so empty→live never remounts the shell.
   */
  readonly onStartRun?: (
    request: RunStartRequest,
  ) => Promise<string | null> | string | null;
  /**
   * Host-injected empty-state composer slot (FR-3.25). When provided and there
   * is no active run, the cockpit renders the design's "What should we run
   * first?" rich composer here (hero + starter chips + AssistantComposer, model
   * pill, Tools, attach, send) instead of the plain goal card — the host mounts
   * the shared `OnboardingComposer` bound to its substrate ports and wires the
   * send to `ctx.onStartRun`. Omitted → the plain `RunEmptyState` fallback (so
   * a substrate without composer wiring still gets an honest goal box).
   */
  readonly renderEmptyComposer?: (ctx: RunEmptyComposerCtx) => ReactNode;
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
    /**
     * desktop-run-identity §D3 — the cockpit's ONE dispatch. The injected in-chat
     * composer calls this to start a run; it binds the live session (via
     * `useRunSession.bindRun`) exactly like the empty-state composer, so turn 1 and
     * turn N share a single path and a 2nd message can never run unbound (the bug
     * where the in-chat composer POSTed a run whose id the cockpit never saw).
     * Takes the rich {@link RunStartRequest} so the in-chat composer can carry
     * attachments through the same path, and returns a promise the composer can
     * await — a rejection routes to the composer's own error notice.
     */
    readonly dispatch: (request: RunStartRequest) => Promise<void>;
    /**
     * WC-P3 (AD-4) — true while the bound run is cancellable; the injected
     * composer swaps its send button for Stop. Cockpit-derived from
     * `useRunSession.runStatus` (optimistically false the instant Stop is
     * pressed), so both substrates light up cancel with no dedicated port.
     */
    readonly running: boolean;
    /**
     * WC-P3 (AD-4) — cancel the bound run. Best-effort Transport POST; the
     * cockpit owns the optimistic settle + the trailing `run_cancelled`
     * reconciliation, so the composer only has to wire this to its Stop control.
     */
    readonly onCancel: () => void;
  }) => ReactElement | null;
  /**
   * WC-P5a (AD-6/AD-7): host launcher for the mid-run `mcp_auth` Connect card.
   * Forwarded verbatim to `TcChat.mcpAuthPort`; when an approval is an `mcp_auth`
   * gate / `mcp_discovery:` suggestion the in-chat card renders Connect / Skip
   * wired to this port (`beginAuth` / `skipAuth`) instead of Approve/Reject, so
   * the connector-auth gate NEVER resolves via the `/decision` POST (`mcp_auth`
   * resolves via a host `mcp_auth_resolved` decision after OAuth returns — P5b;
   * a `mcp_discovery:` row is not persisted, so `/decision` 404s). Omitted → the
   * card degrades to an inert (but visible) gate. The redirect / `sessionStorage`
   * stash / `/mcp/oauth/callback` route stay host-owned (NFR-5).
   */
  readonly mcpAuthPort?: McpAuthPort;
  /**
   * WC-P6a (AD-11): the host-supplied markdown chip renderer, forwarded verbatim
   * to the in-chat `TcChat` (its `components.a` slot routes the citation-remark
   * plugin's `#cite-ord:` / `#cite:` anchors to the host's chip dispatcher). The
   * cockpit mounts a `CitationsProvider` (fed by the pure `projectCitations`
   * selector) around that TcChat, so the host chip wrappers resolve `[[N]]` /
   * `[c<id>]` chips against the single event projection. Omitted → assistant
   * markdown renders without resolved chips (unchanged from before).
   */
  readonly markdownComponents?: MarkdownTextProps["components"];
  /**
   * WC-P6a: optional nav callback fired when an `[[N]]` chip is clicked, with the
   * resolved synthetic `citation_id` (`tool:<source_tool_call_id>`). Host-owned
   * (nav is substrate) so the package never navigates; omitted → the chip falls
   * back to plain anchor navigation (`#tool-call-<callId>`).
   */
  readonly onOrdinalSelect?: (citationId: string) => void;
  /**
   * WC-P6c (FR-9): Sources-tab rail seams, threaded verbatim to
   * `RunWorkspaceRail`. `onSelectSource` / `onJumpToChatSource` are host-owned
   * nav (scroll the transcript to the cited chip); `SourceRowComponent` lets the
   * web host inject its hover-preview-wired row. All optional — omitted → the
   * rail renders the plain `SourceRow` with no nav (unchanged).
   */
  readonly onSelectSource?: (source: SourceEntry) => void;
  readonly onJumpToChatSource?: (source: SourceEntry) => void;
  readonly SourceRowComponent?: SourceRowSlot;
  /**
   * Generative Surfaces v2 canvas (PRD-B1). When `true`, the surface-tab strip
   * is derived from the v2 Work Ledger fold (`projectLedger` over the SAME
   * `session.events`) instead of the v1 `projectSurfaceTabs`, and the canvas
   * hydrates content from the SurfaceStore endpoint. Default `false` ⇒ the
   * cockpit is byte-identical to today (SDR §11 strictness — v2 tabs come ONLY
   * from ledger events, never mixed with v1 envelope surfaces). The host reads
   * the client flag (`isSurfacesV2CanvasEnabled` web / `isSurfacesV2Enabled`
   * desktop), enabled together with the runtime `SURFACES_V2` flag.
   */
  readonly surfacesV2?: boolean;
  /**
   * Generative Surfaces v2 (PRD-B2) — host clipboard + file-save for the raw
   * fallback's Copy / Download, forwarded verbatim to `ThreadCanvas`. Substrate-
   * owned (the package never touches the clipboard/filesystem). Optional; omitted
   * → the raw fallback's buttons render disabled. Only consulted when `surfacesV2`.
   */
  readonly onCopyText?: (text: string) => Promise<void>;
  readonly onSaveFile?: (text: string, filename: string) => Promise<void>;
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
    renderEmptyComposer,
    mcpAuthPort,
    markdownComponents,
    onOrdinalSelect,
    onSelectSource,
    onJumpToChatSource,
    SourceRowComponent,
    surfacesV2 = false,
    onCopyText,
    onSaveFile,
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
  // WC-P3 (AD-4/AD-5): the run the user just pressed Stop on. Set optimistically
  // so `running` flips false in the SAME tick — the Stop button doesn't sit
  // there looking dead between the click and the trailing `run_cancelling` /
  // `run_cancelled` SSE frame that makes runStatus terminal. Scoped by run id
  // (compared against the bound run) so it self-clears when a new run binds; the
  // conversation-reset effect also clears it. We deliberately do NOT clear
  // `boundRunId` (unlike ChatScreen's items model): the cockpit mounts the empty
  // "What should we run?" state whenever `session.runId === null`, so clearing it
  // would flash away the very conversation being cancelled — and the cockpit has
  // no items-scan auto-resume, so head-resolution (`prev ?? head`, once per
  // conversation) can never re-bind the run anyway.
  const [cancellingRunId, setCancellingRunId] = useState<string | null>(null);
  // WC-P4 (AD-9): the just-sent user turn, echoed optimistically in the
  // transcript from dispatch until the run-start re-seed absorbs the persisted
  // turn. Set here (the ONE dispatch), read by useRunTranscript; not rolled back
  // on a failed send (the re-seed / next dispatch replaces it, the reset clears).
  const [pendingUserMessage, setPendingUserMessage] = useState<string | null>(
    null,
  );

  // Monotonic token identifying the current start attempt. Bumped whenever the
  // conversation resets (below), so an in-flight start's async continuation can
  // detect that the cockpit moved on and drop its result — mirroring
  // `useRunSession`'s `cancelled` guard. Without it, a run POST that resolves
  // AFTER the user navigated to another conversation would bind that run into
  // the wrong conversation (a rare load-time race today; a real leak later).
  const startTokenRef = useRef(0);

  // A new conversation clears the last-started run so a stale id never streams
  // against it (mirrors `useRunSession`'s own per-conversation reset).
  useEffect(() => {
    // Invalidate any in-flight start so its late continuation can't re-bind a
    // run into this now-different conversation.
    startTokenRef.current += 1;
    setStartedRunId(null);
    setIsStartingRun(false);
    setStartedGoal(null);
    setStartError(null);
    // WC-P3: never carry an optimistic cancel across conversations.
    setCancellingRunId(null);
    // WC-P4: never echo a prior conversation's user turn into a new one.
    setPendingUserMessage(null);
    // PRD-04: a new conversation starts from a clean surface strip.
    setPinnedUri(null);
    setClosedUris(EMPTY_CLOSED_URIS);
    // PRD-09c: never carry an open edit overlay across conversations.
    setEditingDiffId(null);
    // Surfaces v2: a new conversation starts from a clean gate/toast state.
    setGatePolicies(EMPTY_GATE_POLICIES);
    setUpgradedSurface(null);
    prevTierRef.current = new Map();
  }, [conversationId]);

  const session = useRunSession({
    conversationId,
    // Only a deep-linked / host-supplied runId seeds the session; a freshly
    // dispatched run binds through `session.bindRun` (the ONE sink, §D3), not this
    // prop — so the empty→live and turn-N transitions share the same binding path.
    runId: explicitRunId,
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

  // Generative Surfaces v2 mount pass. All strictly gated on `surfacesV2` (flag
  // off ⇒ inert + never rendered, so the cockpit is byte-identical to today).
  //
  // C2: the reviewer's per-gate write-policy choice (defaults `ask_first`), held
  // locally so the gate card's radio is controlled; the choice is best-effort
  // PATCHed to the connector and rides the OAuth resolve server-side.
  const [gatePolicies, setGatePolicies] =
    useState<ReadonlyMap<string, LedgerGateWritePolicy>>(EMPTY_GATE_POLICIES);
  // E2/F3: a monotonic nonce the "N waiting" counter chip bumps to command the
  // rail onto the Approvals tab (one-directional; the rail reacts to increases).
  const [approvalsFocusSignal, setApprovalsFocusSignal] = useState(0);
  // B3: the surface whose effective tier just upgraded generic → shaped (drives
  // the non-modal ViewUpgradeToast). `null` = no pending upgrade toast.
  const [upgradedSurface, setUpgradedSurface] = useState<{
    readonly surfaceId: string;
    readonly ledgerId: string;
  } | null>(null);
  // B3: the last effective tier seen per surface — the generic→shaped edge
  // detector for the toast. A ref (not state) so it never triggers a re-render.
  const prevTierRef = useRef<Map<string, LedgerViewTier>>(new Map());

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
  const { selectRun, bindRun } = session;
  const handleStartRun = useCallback(
    (request: RunStartRequest): Promise<void> => {
      const goal = request.goal.trim();
      const hasAttachments = (request.attachments?.length ?? 0) > 0;
      if (isStartingRun) {
        return Promise.resolve();
      }
      // The rich composer may send with an attachment and no text; only a truly
      // empty submit (no goal AND no attachments) is a no-op.
      if (goal === "" && !hasAttachments) {
        return Promise.resolve();
      }
      // Readiness gate (Issue 1): never fire a start that is guaranteed to fail
      // with a configuration error. The composer stays LIVE with no model
      // configured — pressing send must not be a silent no-op, so answer in the
      // composer's OWN inline error strip (the design's `.fr-cerr`) with the
      // `configuration_error` code that drives its "Add a key" CTA, and skip the
      // doomed network call entirely.
      if (!modelReady) {
        setStartError({
          message: "No model configured — connect one to run.",
          code: "configuration_error",
        });
        return Promise.resolve();
      }
      // Tag this attempt; the conversation-reset effect bumps the ref, so a
      // continuation that runs after a conversation switch drops its result.
      const startToken = startTokenRef.current;
      setIsStartingRun(true);
      setStartError(null);
      // Bridge the header until the run list re-resolves; an attachment-only
      // start has no goal text, so leave it null (→ "Untitled run" fallback).
      setStartedGoal(goal !== "" ? goal : null);
      // WC-P4 (AD-9): echo the user's turn into the transcript at once, so the
      // send is never a beat of silence before the run-start re-seed lands.
      setPendingUserMessage(goal !== "" ? goal : null);
      const normalized: RunStartRequest = { ...request, goal };
      const start = onStartRun
        ? Promise.resolve(onStartRun(normalized))
        : transport
            .request<unknown>({
              method: "POST",
              path: "/v1/agent/runs",
              body: buildRunCreateBody(conversationId, normalized),
            })
            .then((payload) => runIdFromCreateResponse(payload));
      // Return the promise so the in-chat composer can await it and route a
      // rejection to its own error notice (§D3). The empty-state composer does
      // NOT await — it reads `startError` (set below) instead — so its caller
      // swallows the rejection to avoid an unhandled promise.
      return start
        .then((newRunId) => {
          // The cockpit switched conversations mid-flight — drop this result so
          // it can't stream a stale run into the new conversation.
          if (startToken !== startTokenRef.current) {
            return;
          }
          if (newRunId !== null && newRunId !== undefined && newRunId !== "") {
            setStartedRunId(newRunId as RunId);
            // The ONE bind sink (§D3): binding here is what flips the session live
            // for BOTH the empty-state and the in-chat composer, so a 2nd message
            // streams exactly like the first. setStartedRunId only bridges the
            // header goal; useRunSession no longer reads it as the run source.
            bindRun(newRunId);
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
          if (startToken !== startTokenRef.current) {
            return;
          }
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
          // Re-throw so the in-chat composer's onSubmitError channel fires too.
          throw err;
        })
        .finally(() => {
          if (startToken !== startTokenRef.current) {
            return;
          }
          setIsStartingRun(false);
        });
    },
    [conversationId, isStartingRun, modelReady, onStartRun, transport, bindRun],
  );

  // The plain fallback composer (`RunEmptyState`) sends a bare goal string; wrap
  // it into the shared `RunStartRequest` seam.
  const handleStartGoal = useCallback(
    (goal: string): void => {
      // The empty-state composer reads `startError` for failures, so swallow the
      // rejection here to avoid an unhandled promise (the in-chat composer awaits
      // handleStartRun directly and routes rejections to its own notice — §D3).
      void handleStartRun({ goal }).catch(() => {});
    },
    [handleStartRun],
  );

  // Clear the inline start error (dismiss / retry) — handed to the empty-state
  // composer via `RunEmptyComposerCtx.dismissError`.
  const clearStartError = useCallback((): void => setStartError(null), []);

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
      // Surfaces v2: a run switch resets the gate/toast state too.
      setGatePolicies(EMPTY_GATE_POLICIES);
      setUpgradedSurface(null);
      prevTierRef.current = new Map();
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
      // An attachment-only start has no goal text (`startedGoal === null`); a
      // run IS attached, so the header must still claim it — "STANDBY" over a
      // subscribed run is a lie (design review) — hence the generic fallback,
      // never null (null → idle copy).
      return startedGoal ?? "Untitled run";
    }
    // A run IS attached but carries no goal text (explicit runId binding, or
    // a list entry without a goal). Same honest generic title rather than null.
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

  // Workstream D: the main-agent tool-call cards, projected off the SAME
  // `session.events` (FR-3.3 — no second subscription/projector). Feeds the
  // inline tool-call card in TcChat so a ~6s `web_search` shows a running→done
  // card in the transcript flow instead of dropping the tool activity entirely.
  // Subagent tool calls are excluded upstream (they belong to the Agents views).
  const toolCalls = useMemo(
    () => projectToolCalls(session.events),
    [session.events],
  );

  // WC-P6a (AD-11): the run-scoped citation registries, projected off the SAME
  // `session.events` (FR-3.3 — no second subscription/projector). Feeds the
  // `CitationsProvider` mounted around the single TcChat so the host chip
  // renderer resolves `[[N]]` / `[c<id>]` chips against it.
  const citationProjection = useMemo(
    () => projectCitations(session.events),
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
    // WC-P4 (AD-9): optimistic user echo until the run-start re-seed absorbs it.
    pendingUserMessage,
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

  // PRD-B3: the two view-lifecycle mutations. Both ride the Transport port (no
  // bare fetch/window) and are keyed on `surface_id` + the owning `run_id`
  // (SDR §4 query param). The resulting `view.derived` / `view.preference`
  // events arrive on the ONE run stream and fold in — no second subscription.
  const handleRegenerateView = useCallback(
    (surfaceId: string): void => {
      const runId = session.runId;
      if (runId === null || runId === "") return;
      void transport
        .request({
          method: "POST",
          path: `/v1/agent/surfaces/${encodeURIComponent(
            surfaceId,
          )}/regenerate?run_id=${encodeURIComponent(runId)}`,
          body: {},
        })
        .catch(() => {
          /* the resulting view.derived SSE frame is the authority */
        });
    },
    [transport, session.runId],
  );
  const handleSetViewPreference = useCallback(
    (surfaceId: string, keep: "generic" | "shaped"): void => {
      const runId = session.runId;
      if (runId === null || runId === "") return;
      void transport
        .request({
          method: "POST",
          path: `/v1/agent/surfaces/${encodeURIComponent(
            surfaceId,
          )}/view-preference?run_id=${encodeURIComponent(runId)}`,
          body: { keep },
        })
        .catch(() => {
          /* the resulting view.preference SSE frame is the authority */
        });
    },
    [transport, session.runId],
  );

  // PRD-B4: the user-invited "Suggest a shape". `run_id` rides the BODY (an
  // untyped-dict passthrough the facade stamps org/user onto — SDR §4); the
  // resulting shape.requested/shape.resolved (+ view.derived on success) events
  // arrive on the ONE run stream and fold in — no second subscription.
  const handleShapeRequest = useCallback(
    (surfaceId: string): void => {
      const runId = session.runId;
      if (runId === null || runId === "") return;
      void transport
        .request({
          method: "POST",
          path: `/v1/agent/surfaces/${encodeURIComponent(surfaceId)}/shape-request`,
          body: { run_id: runId },
        })
        .catch(() => {
          /* the resulting shape.resolved SSE frame is the authority */
        });
    },
    [transport, session.runId],
  );

  // WC-P3 (AD-4): the in-chat composer shows Stop while the bound run is
  // cancellable and no cancel is in flight (server `cancelling` state OR our
  // optimistic overlay for THIS run). `cancellingRunId` is compared to the bound
  // run so a stale flag from a prior run can never suppress Stop on a new one.
  const boundRunId = session.runId;
  const running =
    boundRunId !== null &&
    session.runStatus !== null &&
    CANCELLABLE_RUN_STATUSES.has(session.runStatus) &&
    cancellingRunId !== boundRunId;

  // Cancel the bound run — cockpit-owned, no dedicated port (AD-4). Optimistically
  // flips `running` false via `cancellingRunId` (Stop hides at once), then POSTs
  // cancel best-effort through the Transport port, mirroring `resolveApproval`: a
  // failure leaves the optimistic state and the trailing `run_cancelled` SSE frame
  // is the authority. We keep `boundRunId` bound (AD-5) so the transcript stays and
  // the terminal frame reconciles it — Stop cannot re-arm (running stays false
  // while this run is bound, and nothing re-binds it).
  const handleCancel = useCallback((): void => {
    if (boundRunId === null) {
      return;
    }
    setCancellingRunId(boundRunId);
    void transport
      .request({
        method: "POST",
        path: `/v1/agent/runs/${boundRunId}/cancel`,
      })
      .catch(() => {
        /* optimistic: the SSE `run_cancelled` frame reconciles the truth */
      });
  }, [boundRunId, transport]);
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
  // PRD-B1: the v2 Work Ledger fold — a pure PEER of `projectSurfaceTabs` over
  // the SAME `session.events` array (one-projector invariant, FR-3.3). Computed
  // unconditionally so the hydration hook can read `ledger.lastLedgerSeq`; the
  // strip only USES it when `surfacesV2` is on.
  const ledger = useMemo(() => projectLedger(session.events), [session.events]);
  // SDR §11 strictness: flag on ⇒ tabs come ONLY from ledger events; flag off ⇒
  // the v1 selector, byte-identical to today. Never mix the two strips.
  const surfaceTabList = useMemo(
    () =>
      surfacesV2
        ? ledgerTabsAsSurfaceTabs(ledger)
        : projectSurfaceTabs(session.events),
    [surfacesV2, ledger, session.events],
  );
  // Content hydration for the v2 canvas (SurfaceStore endpoint via Transport).
  // Called unconditionally (Rules of Hooks); inert when `surfacesV2` is false
  // (`enabled: false` ⇒ no request, no state churn).
  const hydration = useSurfacesV2(
    transport,
    session.runId,
    ledger.lastLedgerSeq,
    surfacesV2 === true,
  );
  // The v2 surface-state resolver handed to ThreadCanvas ONLY when `surfacesV2`.
  // Uses the exported inverse to recover the surface_id — never hand-parses.
  const resolveSurfaceState = useMemo(
    () =>
      surfacesV2
        ? (uri: string) => {
            const id = surfaceIdForTabUri(uri);
            return id !== null ? hydration.stateFor(id) : undefined;
          }
        : undefined,
    [surfacesV2, hydration],
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

  // PRD-B3: the active surface's folded view-lifecycle state (tier ladder +
  // preference + regen), read off the SAME ledger fold — no second projector.
  // Null off the v2 path or before a `view.derived` lands.
  const activeViewState = useMemo(() => {
    if (!surfacesV2) return null;
    const id = surfaceIdForTabUri(activeUri);
    if (id === null) return null;
    return ledger.surfaces.get(id)?.viewState ?? null;
  }, [surfacesV2, activeUri, ledger]);

  // PRD-B4: the active surface's folded "Suggest a shape" state (idle by default).
  const activeShapeRequest = useMemo<LedgerShapeRequestState>(() => {
    if (!surfacesV2) return "idle";
    const id = surfaceIdForTabUri(activeUri);
    if (id === null) return "idle";
    return ledger.surfaces.get(id)?.shapeRequest ?? "idle";
  }, [surfacesV2, activeUri, ledger]);

  // ============================================================
  // Generative Surfaces v2 — integration mount pass
  // ============================================================
  // Every projection/hook below is a pure PEER of the ledger fold over the SAME
  // `session.events` (the one-projector invariant, FR-3.3) or one Transport-fed
  // fetch. All are gated on `surfacesV2` — flag off ⇒ empties/inert, so the
  // cockpit is byte-identical to today (memos hold a stable empty reference; the
  // pending-work hook is `enabled: false`, issuing no request).

  // E1: the run receipt (null until `receipt.emitted`) + the read-fold sources.
  const receiptProjection = useMemo(
    () => (surfacesV2 ? projectReceipt(session.events) : EMPTY_RECEIPT),
    [surfacesV2, session.events],
  );
  const ledgerSourcesProjection = useMemo<LedgerSourcesProjection | null>(
    () => (surfacesV2 ? projectLedgerSources(session.events) : null),
    [surfacesV2, session.events],
  );

  // E2: this run's live pending cards (open gates + held stages), a peer of
  // `projectApprovals`/`projectLedger`. `usePendingWork` merges these with the
  // cross-run `GET /v1/agent/pending-work` fetch (the open run's live cards win).
  const liveCards = useMemo(
    () =>
      surfacesV2
        ? projectPendingCards(session.events, session.runId)
        : EMPTY_CARDS,
    [surfacesV2, session.events, session.runId],
  );
  const pendingWork = usePendingWork(
    transport,
    surfacesV2 && enabled,
    session.runId,
    liveCards,
    ledger.lastLedgerSeq,
  );

  // D1/D3: index the folded stages by the surface they target so the canvas can
  // mount the staged-draft / staged-table surface for the active tab.
  const stageBySurfaceId = useMemo(() => {
    const map = new Map<string, LedgerStagedWrite>();
    if (!surfacesV2) return map;
    for (const stage of ledger.stages.values()) {
      if (stage.surfaceId !== "") map.set(stage.surfaceId, stage);
    }
    return map;
  }, [surfacesV2, ledger]);

  // D1/D3 stage decision helper — every stage mutation rides the Transport port
  // (no bare fetch/window) keyed on `stage_id` + the owning `run_id` (SDR §6).
  // The resulting ledger events arrive on the ONE run stream and fold in — no
  // second subscription. Best-effort: a failure leaves the optimistic ledger
  // (the trailing event reconciles), mirroring `resolveApproval`.
  const stageRunId = session.runId as string | null;
  const postStageDecision = useCallback(
    (stageId: string, body: Record<string, unknown>): void => {
      if (stageRunId === null || stageRunId === "") return;
      void transport
        .request({
          method: "POST",
          path: `/v1/agent/stages/${encodeURIComponent(
            stageId,
          )}/decisions?run_id=${encodeURIComponent(stageRunId)}`,
          body,
        })
        .catch(() => {
          /* optimistic: the trailing decision.recorded frame is the authority */
        });
    },
    [transport, stageRunId],
  );
  const handleStageApprove = useCallback(
    (stageId: string, rev: number): void =>
      postStageDecision(stageId, { decision: "approve", rev }),
    [postStageDecision],
  );
  const handleStageReject = useCallback(
    (stageId: string, rev: number): void =>
      postStageDecision(stageId, { decision: "reject", rev }),
    [postStageDecision],
  );
  const handleStageRestore = useCallback(
    (stageId: string): void =>
      postStageDecision(stageId, { decision: "restore" }),
    [postStageDecision],
  );
  const handleRowDecision = useCallback(
    (stageId: string, decision: "approve" | "hold", rowKey: string): void =>
      postStageDecision(stageId, { decision, row_keys: [rowKey] }),
    [postStageDecision],
  );
  const handleStageEdit = useCallback(
    (stageId: string, baseRev: number, contentText: string): void => {
      if (stageRunId === null || stageRunId === "") return;
      void transport
        .request({
          method: "POST",
          path: `/v1/agent/stages/${encodeURIComponent(
            stageId,
          )}/revisions?run_id=${encodeURIComponent(stageRunId)}`,
          body: { base_rev: baseRev, content_text: contentText },
        })
        .catch(() => {
          /* optimistic: the trailing revision.added frame is the authority */
        });
    },
    [transport, stageRunId],
  );
  const handleStageApply = useCallback(
    (stageId: string, rev: number, rowKeys: readonly string[]): void => {
      if (stageRunId === null || stageRunId === "") return;
      void transport
        .request({
          method: "POST",
          path: `/v1/agent/stages/${encodeURIComponent(
            stageId,
          )}/apply?run_id=${encodeURIComponent(stageRunId)}`,
          body: { rev, row_keys: rowKeys },
        })
        .catch(() => {
          /* optimistic: the trailing write.applied frame is the authority */
        });
    },
    [transport, stageRunId],
  );

  // C2 gate callbacks. Connect / Skip fire the host `McpAuthPort` (the SAME
  // mid-run OAuth launcher the in-chat `mcp_auth` card uses); absent → inert but
  // visible (desktop has no mid-run launcher wired yet). The write-policy choice
  // is held locally (controlled radio) AND best-effort PATCHed to the connector.
  const handleGateConnect = useCallback(
    (serverId: string): void => {
      mcpAuthPort?.beginAuth(serverId);
    },
    [mcpAuthPort],
  );
  const handleGateSkip = useCallback(
    (serverId: string): void => {
      mcpAuthPort?.skipAuth(serverId);
    },
    [mcpAuthPort],
  );
  const handleGatePolicyChange = useCallback(
    (gateId: string, serverId: string, policy: LedgerGateWritePolicy): void => {
      setGatePolicies((prev) => {
        if (prev.get(gateId) === policy) return prev;
        const next = new Map(prev);
        next.set(gateId, policy);
        return next;
      });
      if (serverId === "") return;
      void transport
        .request({
          method: "PATCH",
          path: `/v1/connectors/${encodeURIComponent(serverId)}/write-policy`,
          body: { write_policy: policy },
        })
        .catch(() => {
          /* best-effort: the authoritative posture is the gate.resolved frame */
        });
    },
    [transport],
  );

  // E2 rail routers. Review pins the card's target surface (a stage card carries
  // `surfaceId`; a gate card has none → no-op, its card is already in the canvas).
  // Open-run rebinds the cockpit to the picked run when it lives in this
  // conversation (cross-conversation nav is a host concern, out of this pass).
  const handleReviewCard = useCallback(
    (card: PendingCard): void => {
      if (card.surfaceId === null) return;
      const surface = ledger.surfaces.get(card.surfaceId);
      if (surface !== undefined) setPinnedUri(tabUriForSurface(surface));
    },
    [ledger],
  );
  const handleOpenRun = useCallback(
    (agent: PendingAgentRow): void => {
      if (agent.run_id !== stageRunId) selectRun(agent.run_id);
    },
    [selectRun, stageRunId],
  );
  const handleOpenApprovals = useCallback(
    (): void => setApprovalsFocusSignal((n) => n + 1),
    [],
  );

  // E1: the receipt's "Copy receipt" reuses the host clipboard port (B2's
  // `onCopyText`); the package never touches the clipboard itself.
  const copyReceiptText = useCallback(
    (text: string): void => {
      void onCopyText?.(text);
    },
    [onCopyText],
  );

  // The kind-specific v2 surface for the active tab, injected into ThreadCanvas
  // (`renderSurfaceOverride`). Staged writes render their draft/table surface
  // (approve/apply bars composed inside); a receipt surface renders the fold.
  // `null` ⇒ ThreadCanvas takes its default v2 mount (record/message/table via
  // the pure adapter registry). Only meaningful on the v2 path.
  const renderV2Surface = useCallback(
    (uri: string): ReactNode => {
      const id = surfaceIdForTabUri(uri);
      if (id === null) return null;
      const stage = stageBySurfaceId.get(id);
      if (stage !== undefined) {
        if (stage.rows !== null) {
          return (
            <TcStagedTableSurface
              stage={stage}
              onRowDecision={handleRowDecision}
              onApply={handleStageApply}
            />
          );
        }
        return (
          <TcStagedDraftSurface
            stage={stage}
            bodyText={draftBodyText(hydration.stateFor(id))}
            onSubmitEdit={handleStageEdit}
            onApprove={handleStageApprove}
            onReject={handleStageReject}
            onRestore={handleStageRestore}
          />
        );
      }
      const surface = ledger.surfaces.get(id);
      if (surface?.kind === "receipt" && receiptProjection.receipt !== null) {
        return (
          <ReceiptSurface
            receipt={receiptProjection.receipt}
            emittedSeq={receiptProjection.emittedSeq}
            onCopyText={copyReceiptText}
          />
        );
      }
      return null;
    },
    [
      stageBySurfaceId,
      ledger,
      hydration,
      receiptProjection,
      handleRowDecision,
      handleStageApply,
      handleStageEdit,
      handleStageApprove,
      handleStageReject,
      handleStageRestore,
      copyReceiptText,
    ],
  );

  // B3: detect a generic → shaped effective-tier upgrade for any surface and
  // raise the non-modal ViewUpgradeToast. Pure edge detection over the ledger
  // fold (a ref of the last-seen tier), so it never opens a second projector.
  useEffect(() => {
    if (!surfacesV2) return;
    const prev = prevTierRef.current;
    const next = new Map<string, LedgerViewTier>();
    let upgraded: { surfaceId: string; ledgerId: string } | null = null;
    for (const [id, surface] of ledger.surfaces) {
      const tier = surface.viewState?.effectiveTier ?? null;
      if (tier === null) continue;
      next.set(id, tier);
      const before = prev.get(id);
      if (before !== undefined && before !== "shaped" && tier === "shaped") {
        upgraded = { surfaceId: id, ledgerId: surface.ledgerId };
      }
    }
    prevTierRef.current = next;
    if (upgraded !== null) setUpgradedSurface(upgraded);
  }, [surfacesV2, ledger]);

  const dismissUpgradeToast = useCallback(
    (): void => setUpgradedSurface(null),
    [],
  );
  const keepGenericFromToast = useCallback(
    (surfaceId: string): void => {
      handleSetViewPreference(surfaceId, "generic");
      setUpgradedSurface(null);
    },
    [handleSetViewPreference],
  );

  // E2: the rail's cross-run pending queue + fleet inputs (undefined when off ⇒
  // the rail is byte-identical). Open-run marks "This run" against the bound run.
  const railPendingV2 = surfacesV2
    ? {
        cards: pendingWork.cards,
        agents: pendingWork.agents,
        onReview: handleReviewCard,
        onOpenRun: handleOpenRun,
        currentRunId: stageRunId,
      }
    : undefined;

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

  // desktop-run-identity §D3 — inject the ONE dispatch into the in-chat composer's
  // ctx. TcChat keeps calling renderComposer with {disabled, placeholder}; this
  // wrapper adds `dispatch` (handleStartRun) so the injected composer starts a run
  // through the SAME path + bind sink as the empty-state composer. Both composers
  // share one send path — a 2nd message can never run unbound (kills that bug).
  // WC-P3 (AD-4): the same wrapper hands down the cockpit-owned run state
  // (`running`) + `onCancel`, so the injected composer swaps send↔Stop without a
  // dedicated port — lighting up cancel on BOTH substrates.
  const renderComposerWithDispatch = useMemo(
    () =>
      renderComposer === undefined
        ? undefined
        : (ctx: { readonly disabled: boolean; readonly placeholder: string }) =>
            renderComposer({
              ...ctx,
              dispatch: handleStartRun,
              running,
              onCancel: handleCancel,
            }),
    [renderComposer, handleStartRun, running, handleCancel],
  );

  const chatSlot = (
    // WC-P6a (AD-11): the citation registry provider wraps the ONE TcChat so the
    // host-supplied `markdownComponents` chip wrappers resolve chips against the
    // pure `projectCitations` output. The provider component is substrate-agnostic
    // (pure React context); the nav-aware chip node + `onOrdinalSelect` stay
    // host-owned. Omitting `markdownComponents` leaves chips unresolved (chip
    // wrappers read the same context either way, so mounting it is always safe).
    <CitationsProvider
      citations={citationProjection.citations}
      byRun={citationProjection.byRun}
      terminalRuns={citationProjection.terminalRuns}
      linksByRun={citationProjection.linksByRun}
      activeRunId={citationProjection.activeRunId}
      {...(onOrdinalSelect !== undefined ? { onOrdinalSelect } : {})}
    >
      <TcChat
        conversationId={conversationId as unknown as string}
        mode={mode}
        messages={transcriptMessages}
        // WC-P6a: the host chip dispatcher (`{ a: MarkdownLink }`) — resolves
        // `[[N]]` / `[c<id>]` anchors against the provider above.
        markdownComponents={markdownComponents}
        fleets={subagentProjection.fleets}
        // Workstream D: inline tool-call cards, interleaved into the transcript
        // by the point each tool ran (running spinner → done/error).
        toolCalls={toolCalls}
        // PR-3.10: in-chat ApprovalCard (Studio) / conf-card (Focus) + receipts.
        approvals={chatApprovals}
        onApprove={handleApprove}
        onReject={handleReject}
        // WC-P5a (AD-6/AD-7): the MCP-OAuth launcher. TcChat renders the Connect
        // card (→ this port) for `mcp_auth` gates / `mcp_discovery:` suggestions
        // instead of Approve/Reject, keeping them off the `/decision` POST. Absent
        // → the card renders inert (host wires the launcher in P5b).
        mcpAuthPort={mcpAuthPort}
        // Host composer seam: desktop mounts the full AssistantComposer here. The
        // dispatch-injecting wrapper (§D3) makes its send bind the live session.
        renderComposer={renderComposerWithDispatch}
      />
    </CitationsProvider>
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
      // WC-P6c (FR-9): Sources-tab seams — host-owned nav + the web preview-wired
      // row. Optional; omitted → the plain SourceRow with no nav.
      onSelectSource={onSelectSource}
      onJumpToChatSource={onJumpToChatSource}
      SourceRowComponent={SourceRowComponent}
      approvalsQueue={approvalsQueue}
      onApprove={handleApprove}
      onReject={handleReject}
      scrubbed={isScrubbed}
      // Surfaces v2 (E1/E2): the read-fold Sources tab, the cross-run pending
      // queue + fleet, and the header chip's "jump to Approvals" signal. All
      // null/undefined when the flag is off ⇒ the rail is byte-identical.
      ledgerSources={ledgerSourcesProjection}
      pendingV2={railPendingV2}
      focusApprovalsSignal={surfacesV2 ? approvalsFocusSignal : undefined}
    />
  );

  // Extracted so the v2 canvas can WRAP it (gate-card region + upgrade toast)
  // without duplicating the prop list, while the flag-off path renders it bare —
  // byte-identical to today (the wrapper divs exist only on the v2 branch).
  const canvasEl = (
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
      // PRD-B1: only defined when `surfacesV2` — flag off ⇒ `undefined`,
      // so ThreadCanvas takes its unchanged v1 projection path (byte-
      // identical). Flag on ⇒ the surface column hydrates from the
      // SurfaceStore endpoint via this resolver.
      resolveSurfaceState={resolveSurfaceState}
      // Integration mount pass: the kind-specific v2 surface for the
      // active tab (staged draft/table, receipt). Undefined when the flag
      // is off; returns null for record/message/etc. so ThreadCanvas keeps
      // its default adapter-registry mount for those.
      renderSurfaceOverride={surfacesV2 ? renderV2Surface : undefined}
      // PRD-B2: host clipboard + file-save for the raw fallback's
      // Copy / Download. Only consulted inside the v2 canvas subtree.
      onCopyText={onCopyText}
      onSaveFile={onSaveFile}
      // PRD-B3: the active surface's folded view-lifecycle state + the two
      // Transport-backed mutations. Only meaningful on the v2 path; the
      // toggle renders only when a `view.derived` has landed (viewState set).
      activeViewState={activeViewState}
      onRegenerateView={handleRegenerateView}
      onSetViewPreference={handleSetViewPreference}
      // PRD-B4: the active surface's folded "Suggest a shape" state + the
      // invited-shaping mutation. The button renders on the raw/generic
      // fallback only (a shaped surface hides it).
      activeShapeRequest={activeShapeRequest}
      onShapeRequest={surfacesV2 ? handleShapeRequest : undefined}
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
  );

  // Surfaces v2 (C2): the parked-gate card region + (B3) the upgrade toast,
  // wrapping the extracted canvas. Only built on the v2 path — flag off renders
  // `canvasEl` bare below.
  const v2CanvasBody = (
    <div data-testid="run-v2-canvas-body" style={v2CanvasBodyStyle}>
      {ledger.openGates.length > 0 ? (
        <div data-testid="run-v2-gate-region" style={gateRegionStyle}>
          {ledger.openGates.map((gate) => (
            <TcGateCard
              key={gate.gateId}
              gate={gate}
              writePolicy={gatePolicies.get(gate.gateId) ?? "ask_first"}
              onConnect={handleGateConnect}
              onSkip={handleGateSkip}
              onPolicyChange={(policy) =>
                handleGatePolicyChange(gate.gateId, gate.serverId, policy)
              }
            />
          ))}
        </div>
      ) : null}
      <div style={v2CanvasThreadStyle}>{canvasEl}</div>
      {upgradedSurface !== null ? (
        <div style={toastLayerStyle}>
          <ViewUpgradeToast
            surfaceId={upgradedSurface.surfaceId}
            ledgerId={upgradedSurface.ledgerId}
            onKeepGeneric={keepGenericFromToast}
            onDismiss={dismissUpgradeToast}
          />
        </div>
      ) : null}
    </div>
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
        // WC-P6b: the `● working` pulse chip, derived from the single event
        // projection's run status (no second subscription — FR-3.3). Live →
        // pulses; terminal / null → absent.
        runStatus={session.runStatus}
      />

      {/* Surfaces v2 (C2/E2): the always-on write-posture chip + the "N waiting"
          cross-run counter (hidden at 0). Both gated on the flag — off ⇒ no DOM,
          byte-identical. The counter commands the rail's Approvals tab via the
          one-directional `approvalsFocusSignal`. */}
      {surfacesV2 ? (
        <div data-testid="run-v2-chip-bar" style={v2ChipBarStyle}>
          <PostureChip bypassOn={ledger.bypassFromLedger} />
          <PendingCounterChip
            count={pendingWork.cards.length}
            onClick={handleOpenApprovals}
          />
        </div>
      ) : null}

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
        {/* PR-3.11 (FR-3.25): no active run → the empty/idle composer (never a
            blank ThreadCanvas / placeholder string). When the host injects
            `renderEmptyComposer`, the cockpit shows the design's "What should we
            run first?" rich composer (hero + starter chips + AssistantComposer);
            otherwise the plain `RunEmptyState` goal card. Either way, submitting
            starts a run and binds it via the ONE sink (`handleStartRun` →
            `session.bindRun`, §D3), so the live layout below mounts IN PLACE — the
            shell (this outer div + header) never remounts.

            Gate on transcript-emptiness, NOT just `runId === null` (§D3): reopening
            a FINISHED conversation loads its transcript (by conversationId) while the
            head run is still resolving, so it shows the thread — never a false "NO
            ACTIVE RUN" over a conversation that already has messages. */}
        {session.runId === null && transcriptMessages.length === 0 ? (
          renderEmptyComposer !== undefined ? (
            <div
              data-testid="run-empty-composer"
              style={emptyComposerOuterStyle}
            >
              {/* Readiness is NOT a standing notice here: the rich composer
                  stays live with no model configured, and a send answers in the
                  composer's own inline error strip (handleStartRun sets a
                  `configuration_error` start error → "Add a key" CTA). The
                  plain `RunEmptyState` fallback below keeps its own setup
                  notice, since it has no inline-error idiom of its own. */}
              <div style={emptyComposerColumnStyle}>
                {renderEmptyComposer({
                  onStartRun: handleStartRun,
                  submitting: isStartingRun,
                  startError,
                  dismissError: clearStartError,
                  modelReady,
                  onOpenModelSettings,
                })}
              </div>
            </div>
          ) : (
            <RunEmptyState
              agentName={agentName}
              onSubmitGoal={handleStartGoal}
              submitting={isStartingRun}
              error={startError}
              setupRequired={!modelReady}
              onOpenModelSettings={onOpenModelSettings}
            />
          )
        ) : surfacesV2 ? (
          // v2 canvas: the extracted ThreadCanvas wrapped with the parked-gate
          // region + upgrade toast. Flag off falls to the bare `canvasEl` below.
          v2CanvasBody
        ) : (
          canvasEl
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

/**
 * Build the `POST /v1/agent/runs` body from a {@link RunStartRequest}. Only the
 * selected fields are attached, so a bare `{ goal }` (the plain fallback
 * composer) yields the historical "conversation + goal only" body — byte-
 * unchanged for hosts that never surface the rich composer. Identity (org/user)
 * is derived server-side from the verified session, never sent by the client.
 *
 * Exported so the host binders (desktop `RunBinder`, web `RunRoute`) that own
 * the POST build the SAME shape as the shell's default path — one body builder,
 * no drift.
 */
export function buildRunCreateBody(
  conversationId: ConversationId,
  request: RunStartRequest,
): Record<string, unknown> {
  const body: Record<string, unknown> = {
    conversation_id: conversationId,
    user_input: request.goal,
  };
  if (request.model !== null && request.model !== undefined) {
    body.model = request.model;
  }
  if (request.attachments !== undefined && request.attachments.length > 0) {
    body.attachments = request.attachments;
  }
  // web_search defaults to on at the runtime; only an explicit opt-OUT is worth
  // sending (an explicit `true` is the runtime default, so it is omitted).
  if (request.webSearchEnabled === false) {
    body.web_search_enabled = false;
  }
  if (
    request.connectorScopes !== undefined &&
    Object.keys(request.connectorScopes).length > 0
  ) {
    body.request_context = { connector_scopes: request.connectorScopes };
  }
  return body;
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

// Rich empty composer frame — a scrollable, vertically-centered 640px column
// (mirrors the design's `.fr-main`; self-contained inline styles so the frame
// never depends on onboarding.css being loaded, while the injected composer's
// own `.fr-*` internals do).
const emptyComposerOuterStyle: CSSProperties = {
  height: "100%",
  width: "100%",
  minHeight: 0,
  overflow: "auto",
  display: "flex",
  flexDirection: "column",
};

const emptyComposerColumnStyle: CSSProperties = {
  flex: "1 1 auto",
  display: "flex",
  flexDirection: "column",
  justifyContent: "center",
  gap: 16,
  width: "min(640px, 92%)",
  margin: "0 auto",
  padding: "22px 0",
};

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

// Surfaces v2 — the header chip row (posture chip + "N waiting" counter).
const v2ChipBarStyle: CSSProperties = {
  flexShrink: 0,
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "6px 16px",
  borderBottom: "1px solid var(--color-border, #22252e)",
  background: "var(--color-bg-elevated, #16181f)",
};

// Surfaces v2 — the v2 canvas body: an (optional) parked-gate region stacked
// above the canvas, which fills the remaining height. `position: relative`
// anchors the absolutely-positioned upgrade toast.
const v2CanvasBodyStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  height: "100%",
  minHeight: 0,
  position: "relative",
};

const v2CanvasThreadStyle: CSSProperties = {
  flex: 1,
  minHeight: 0,
  position: "relative",
};

const gateRegionStyle: CSSProperties = {
  flexShrink: 0,
  display: "flex",
  flexDirection: "column",
  gap: 8,
  padding: 16,
  maxHeight: "50%",
  overflow: "auto",
  borderBottom: "1px solid var(--color-border, #22252e)",
};

// The upgrade toast floats over the bottom-right of the canvas (non-modal).
const toastLayerStyle: CSSProperties = {
  position: "absolute",
  right: 16,
  bottom: 16,
  zIndex: 3,
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
