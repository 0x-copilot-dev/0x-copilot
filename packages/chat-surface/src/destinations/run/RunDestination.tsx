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
//   - PR-3.11 empty/multi-run (DONE): `session.runs` still supports internal
//     run rebinding (for direct links and pending-work review), while the
//     cockpit deliberately omits a visible run-history strip. `RunEmptyState`
//     mounts in the canvas slot when
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

// The answer payload an `ask_a_question` card emits. Type-only — the card
// itself is mounted by TcChat; this shell only owns the POST that resumes the run.
import type { QuestionAnswer } from "../../approvals";

import {
  isSourceOpenResultV2,
  type AgentRunStatus,
  type ArtifactKind,
  type CitationSourceRef,
  type ConversationConnectorScopes,
  type ConversationId,
  type ModelSelectionRequest,
  type RunAttachmentRequest,
  type RunId,
  type RunReceiptV2,
  type RuntimeEventReplayResponse,
  type SourceEntry,
  type SourcesProjectionV2,
  type SubagentEntry,
  type SubagentListResponse,
  type SurfaceEdits,
} from "@0x-copilot/api-types";
import { isArtifactTransport } from "@0x-copilot/chat-transport";

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
import {
  projectSubagentActivities,
  projectSubagents,
  type FleetProjection,
} from "../../subagents";
import {
  ThreadCanvas,
  TcChat,
  TcGateCard,
  TcStagedDraftSurface,
  TcStagedTableSurface,
  TcWorkspaceStageSurface,
  ViewUpgradeToast,
  projectSurfaceTabs,
  projectToolCalls,
  projectLedger,
  surfaceIdForTabUri,
  tabUriForSurface,
  type TcTab,
  type ToolCallEntry,
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
import { ReceiptV2LaunchCard, ReceiptV2Surface } from "../../surfaces/receipt";
import { PostureChip } from "./PostureChip";
import { PendingCounterChip } from "./PendingCounterChip";
import { usePendingWork } from "./usePendingWork";
import { usePendingWorkV2 } from "./usePendingWorkV2";
import {
  projectPendingCards,
  type PendingCard,
} from "./pendingCardsProjection";
import type { PendingWorkCardV2 } from "./pendingWorkV2Projection";
import { projectReceiptV2, type ReceiptV2Projection } from "./projectReceiptV2";
import { projectSourcesV2 } from "../../projections/sourcesV2";
import {
  projectLegacyV2Replay,
  type LegacyV2ReplayProjection,
  type PendingAgentRow,
} from "@0x-copilot/api-types";
// PRD-B1: Generative Surfaces v2 content hydration (SurfaceStore endpoint via
// the Transport port). Called unconditionally (Rules of Hooks) but inert when
// `surfacesV2` is false (`enabled: false` ⇒ no request).
import { useSurfacesV2 } from "./useSurfacesV2";
import {
  ArtifactSurface,
  artifactUri,
  parseArtifactSurfaceUri,
} from "../../artifacts";
import type { ArtifactDownloadPort } from "../../ports/ArtifactDownloadPort";
import type {
  WorkspaceApprovalSnapshot,
  WorkspaceStageHost,
} from "../../ports/WorkspaceStageHostPort";
import { CanvasFocusCards } from "./CanvasFocusCards";
import { CanvasLifecyclePanel } from "./CanvasLifecyclePanel";
import { EffectStageCard } from "./EffectStageCard";
import { projectCanvasLifecycle } from "./canvasLifecycle";
import {
  projectMcpEffectStages,
  type McpEffectStageReview,
} from "./effectStageLifecycle";
import {
  projectWorkspaceStageLifecycle,
  type WorkspaceStageReview,
  type WorkspaceStageReviewProjection,
} from "./workspaceStageLifecycle";

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
import { useConnectorConsentStates } from "./useConnectorConsentStates";
// PR-3.11: the empty/idle goal composer (FR-3.25) mounts inside this shell (no
// separate host remount) and binds a freshly-started run via the `runId` seam.
import { RunEmptyState, type StartRunError } from "./RunEmptyState";
import { RunMultiSelect } from "./RunMultiSelect";
// PRD-04: pure selector projecting proposed surface diffs off the SAME single
// canonical event stream (FR-3.3). Feeds the on-surface Approve/Reject controls
// in TcSurfaceMount (via ThreadCanvas.pendingDiff); no second subscription.
import { projectSurfaceDiffs } from "./_surfaceDiffs";
import { RunHeader } from "./RunHeader";
import { RunWorkspaceRail } from "./RunWorkspaceRail";
import { projectFocusPlan } from "./FocusPlan";
import type { SourceRowSlot } from "../../workspace";
import { useRailWidth } from "./useRailWidth";
import { useRunMode, useRunPanelCollapsed } from "./useRunMode";
import { useRunSources } from "./useRunSources";
import { useRunTranscript } from "./useRunTranscript";
import { useRunSession } from "./useRunSession";

const EMPTY_DECISIONS: ReadonlyMap<string, RunApprovalDecision> = new Map();
const EMPTY_CLOSED_URIS: ReadonlySet<string> = new Set();
const EMPTY_EXPLICIT_ARTIFACT_TABS: readonly ExplicitArtifactTab[] = [];
// Generative Surfaces v2 mount-pass empties (flag-off = referentially stable so
// the memos/props never churn when the cockpit is byte-identical to today).
const EMPTY_CARDS: readonly PendingCard[] = [];
const EMPTY_RECEIPT_V2: ReceiptV2Projection = {
  receipt: null,
  available: false,
  chatOnly: false,
  shouldAutoOpen: false,
};

/**
 * Receipt data remains durable/exportable for every run, but the cockpit only
 * offers a visual receipt when it records a consequential outcome. Ordinary
 * chat, tool, and subagent runs already have their useful detail in the
 * transcript and Agents tab; showing a second canvas card for them is noise.
 */
function needsCockpitReceipt(receipt: RunReceiptV2): boolean {
  const { artifacts, effects, gates } = receipt;
  return (
    artifacts.promoted > 0 ||
    effects.proposed > 0 ||
    effects.approved > 0 ||
    effects.applied > 0 ||
    effects.rejected > 0 ||
    effects.partial > 0 ||
    effects.held > 0 ||
    effects.indeterminate > 0 ||
    gates.opened > 0 ||
    gates.resolved > 0 ||
    gates.pending > 0
  );
}
const EMPTY_GATE_POLICIES: ReadonlyMap<string, LedgerGateWritePolicy> =
  new Map();
const EMPTY_WORKSPACE_STAGE_REVIEWS: WorkspaceStageReviewProjection = new Map();
const EMPTY_MCP_EFFECT_STAGE_REVIEWS: ReadonlyMap<
  string,
  McpEffectStageReview
> = new Map();
const EMPTY_SUBAGENT_ARCHIVE: ReadonlyMap<string, SubagentEntry> = new Map();
const EMPTY_FLEET_ARCHIVE: readonly FleetProjection[] = [];
const EMPTY_TOOL_CALL_ARCHIVE: readonly ToolCallEntry[] = [];
// E2 D3: historic rows are selected through a read-only compatibility reader.
// Kept stable while the Studio canvas flag is off so the legacy cockpit remains
// byte-identical and never inspects older surface envelopes on that path.
const EMPTY_LEGACY_V2_REPLAY: LegacyV2ReplayProjection = {
  reader_version: 1,
  mode: "empty",
  surfaces: [],
  quarantined: [],
};
const EFFECT_STAGE_URI_PREFIX = "effect-stage://";
const RECEIPT_V2_URI_PREFIX = "receipt-v2://";
const SOURCE_OPEN_UNAVAILABLE = "This source is no longer available.";

interface ConversationSubagentArchive {
  /** Archive plus any live entries remembered during this cockpit session. */
  readonly subagents: ReadonlyMap<string, SubagentEntry>;
  readonly loading: boolean;
  readonly error: string | null;
}

interface ConversationFleetArchive {
  /** Historic fleet bookends plus the live fleets remembered by this cockpit. */
  readonly fleets: readonly FleetProjection[];
}

interface ConversationToolCallArchive {
  /** Historic main-agent tool cards plus the live cards remembered by this cockpit. */
  readonly toolCalls: readonly ToolCallEntry[];
}

/**
 * The run event stream is intentionally run-scoped: binding a new message's
 * run replaces `session.events`. The Agents tab, however, is conversation
 * scoped. Seed it from the conversation archive and retain every live entry we
 * have already observed; the current stream always wins for the same task.
 */
function useConversationSubagentArchive(
  transport: ReturnType<typeof useTransport>,
  conversationId: ConversationId,
  liveSubagents: ReadonlyMap<string, SubagentEntry>,
): ConversationSubagentArchive {
  const [archived, setArchived] = useState<ReadonlyMap<string, SubagentEntry>>(
    EMPTY_SUBAGENT_ARCHIVE,
  );
  const [rememberedLive, setRememberedLive] = useState<
    ReadonlyMap<string, SubagentEntry>
  >(EMPTY_SUBAGENT_ARCHIVE);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (conversationId === "new") {
      setArchived(EMPTY_SUBAGENT_ARCHIVE);
      setRememberedLive(EMPTY_SUBAGENT_ARCHIVE);
      setLoading(false);
      setError(null);
      return undefined;
    }

    let cancelled = false;
    setArchived(EMPTY_SUBAGENT_ARCHIVE);
    setRememberedLive(EMPTY_SUBAGENT_ARCHIVE);
    setLoading(true);
    setError(null);
    void transport
      .request<SubagentListResponse>({
        method: "GET",
        path: `/v1/agent/conversations/${encodeURIComponent(conversationId)}/subagents`,
      })
      .then((response) => {
        if (cancelled) return;
        setArchived(
          new Map(
            Array.isArray(response?.subagents)
              ? response.subagents.map((entry) => [entry.task_id, entry])
              : [],
          ),
        );
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setError(
          reason instanceof Error && reason.message !== ""
            ? reason.message
            : "Could not load earlier subagents.",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId, transport]);

  useEffect(() => {
    if (conversationId === "new" || liveSubagents.size === 0) return;
    setRememberedLive((previous) => {
      const next = new Map(previous);
      let changed = false;
      for (const [taskId, entry] of liveSubagents) {
        if (next.get(taskId) !== entry) {
          next.set(taskId, entry);
          changed = true;
        }
      }
      return changed ? next : previous;
    });
  }, [conversationId, liveSubagents]);

  const subagents = useMemo(() => {
    const merged = new Map(archived);
    // A task that reached the live stream is fresher than the archive response.
    for (const [taskId, entry] of rememberedLive) merged.set(taskId, entry);
    for (const [taskId, entry] of liveSubagents) merged.set(taskId, entry);
    return merged;
  }, [archived, rememberedLive, liveSubagents]);

  return { subagents, loading, error };
}

/**
 * Keep inline fleet cards conversation-scoped, just like the transcript.
 *
 * `useRunSession.events` deliberately resets when a newer message binds its
 * own run. That is correct for the live stream, but it used to make every
 * completed fleet card above the newest assistant message disappear. The
 * immutable run ledger is the source of truth for those historic cards, so
 * replay the lightweight fleet bookends for prior runs and retain any live
 * fleet already observed before that replay completes.
 */
function useConversationFleetArchive(
  transport: ReturnType<typeof useTransport>,
  conversationId: ConversationId,
  runIds: readonly string[],
  liveFleets: readonly FleetProjection[],
): ConversationFleetArchive {
  const [archived, setArchived] =
    useState<readonly FleetProjection[]>(EMPTY_FLEET_ARCHIVE);
  const [rememberedLive, setRememberedLive] = useState<
    ReadonlyMap<string, FleetProjection>
  >(new Map());

  // `session.runs.map(...)` is intentionally recreated by the shell. Key the
  // replay effect on a stable primitive so recording an archive does not cause
  // a refetch loop.
  const runIdsKey = [...new Set(runIds)].sort().join("\u0000");
  const replayRunIds = useMemo(
    () => (runIdsKey === "" ? [] : runIdsKey.split("\u0000")),
    [runIdsKey],
  );

  useEffect(() => {
    setArchived(EMPTY_FLEET_ARCHIVE);
    setRememberedLive(new Map());
  }, [conversationId]);

  useEffect(() => {
    if (conversationId === "new" || liveFleets.length === 0) return;
    setRememberedLive((previous) => {
      const next = new Map(previous);
      let changed = false;
      for (const fleet of liveFleets) {
        if (next.get(fleet.fleetId) !== fleet) {
          next.set(fleet.fleetId, fleet);
          changed = true;
        }
      }
      return changed ? next : previous;
    });
  }, [conversationId, liveFleets]);

  useEffect(() => {
    if (conversationId === "new") return undefined;
    if (replayRunIds.length === 0) {
      setArchived(EMPTY_FLEET_ARCHIVE);
      return undefined;
    }
    let cancelled = false;
    void Promise.all(
      replayRunIds.map(async (runId) => {
        const response = await transport.request<RuntimeEventReplayResponse>({
          method: "GET",
          path: `/v1/agent/runs/${encodeURIComponent(runId)}/events`,
        });
        return projectSubagents(response.events ?? []).fleets;
      }),
    )
      .then((fleetGroups) => {
        if (!cancelled) setArchived(fleetGroups.flat());
      })
      .catch(() => {
        // Cards already observed live remain visible. A historical replay is a
        // progressive enhancement, so a transient failure must not block chat.
        if (!cancelled) setArchived(EMPTY_FLEET_ARCHIVE);
      });
    return () => {
      cancelled = true;
    };
  }, [transport, conversationId, replayRunIds]);

  const fleets = useMemo(() => {
    const merged = new Map<string, FleetProjection>();
    for (const fleet of archived) merged.set(fleet.fleetId, fleet);
    for (const fleet of rememberedLive.values()) {
      merged.set(fleet.fleetId, fleet);
    }
    for (const fleet of liveFleets) merged.set(fleet.fleetId, fleet);
    return [...merged.values()].sort((left, right) => {
      const leftAt = left.createdAtMs ?? Number.MAX_SAFE_INTEGER;
      const rightAt = right.createdAtMs ?? Number.MAX_SAFE_INTEGER;
      return leftAt === rightAt
        ? left.fleetId.localeCompare(right.fleetId)
        : leftAt - rightAt;
    });
  }, [archived, rememberedLive, liveFleets]);

  return { fleets };
}

/**
 * Keep direct main-agent tool cards conversation-scoped, just like messages
 * and fleet cards.
 *
 * The active run's event tail is deliberately replaced when the next user
 * message starts. That must not erase the completed `web_search` (or other
 * direct tool) card from the earlier turn. Replaying every run's immutable
 * event ledger supplies that history; cards observed live are retained while
 * the replay is in flight, and win for the same call id.
 */
function useConversationToolCallArchive(
  transport: ReturnType<typeof useTransport>,
  conversationId: ConversationId,
  runIds: readonly string[],
  liveToolCalls: readonly ToolCallEntry[],
): ConversationToolCallArchive {
  const [archived, setArchived] = useState<readonly ToolCallEntry[]>(
    EMPTY_TOOL_CALL_ARCHIVE,
  );
  const [rememberedLive, setRememberedLive] = useState<
    ReadonlyMap<string, ToolCallEntry>
  >(new Map());

  // `session.runs.map(...)` is intentionally recreated by the shell. Key the
  // replay effect on a stable primitive so recording an archive does not cause
  // a refetch loop.
  const runIdsKey = [...new Set(runIds)].sort().join("\u0000");
  const replayRunIds = useMemo(
    () => (runIdsKey === "" ? [] : runIdsKey.split("\u0000")),
    [runIdsKey],
  );

  useEffect(() => {
    setArchived(EMPTY_TOOL_CALL_ARCHIVE);
    setRememberedLive(new Map());
  }, [conversationId]);

  useEffect(() => {
    if (conversationId === "new" || liveToolCalls.length === 0) return;
    setRememberedLive((previous) => {
      const next = new Map(previous);
      let changed = false;
      for (const toolCall of liveToolCalls) {
        if (next.get(toolCall.id) !== toolCall) {
          next.set(toolCall.id, toolCall);
          changed = true;
        }
      }
      return changed ? next : previous;
    });
  }, [conversationId, liveToolCalls]);

  useEffect(() => {
    if (conversationId === "new") return undefined;
    if (replayRunIds.length === 0) {
      setArchived(EMPTY_TOOL_CALL_ARCHIVE);
      return undefined;
    }
    let cancelled = false;
    void Promise.all(
      replayRunIds.map(async (runId) => {
        const response = await transport.request<RuntimeEventReplayResponse>({
          method: "GET",
          path: `/v1/agent/runs/${encodeURIComponent(runId)}/events`,
        });
        return projectToolCalls(response.events ?? []);
      }),
    )
      .then((toolCallGroups) => {
        if (!cancelled) setArchived(toolCallGroups.flat());
      })
      .catch(() => {
        // Cards already observed live remain visible. A historical replay is a
        // progressive enhancement, so a transient failure must not block chat.
        if (!cancelled) setArchived(EMPTY_TOOL_CALL_ARCHIVE);
      });
    return () => {
      cancelled = true;
    };
  }, [transport, conversationId, replayRunIds]);

  const toolCalls = useMemo(() => {
    const merged = new Map<string, ToolCallEntry>();
    for (const toolCall of archived) merged.set(toolCall.id, toolCall);
    for (const toolCall of rememberedLive.values()) {
      merged.set(toolCall.id, toolCall);
    }
    for (const toolCall of liveToolCalls) merged.set(toolCall.id, toolCall);
    return [...merged.values()].sort((left, right) => {
      const leftAt = left.createdAtMs ?? Number.MAX_SAFE_INTEGER;
      const rightAt = right.createdAtMs ?? Number.MAX_SAFE_INTEGER;
      return leftAt === rightAt
        ? left.id.localeCompare(right.id)
        : leftAt - rightAt;
    });
  }, [archived, rememberedLive, liveToolCalls]);

  return { toolCalls };
}

/** Add durable subagent records to a replayed fleet whose event log only kept
 * the fleet bookends. This preserves the compact card immediately and fills
 * its expandable child rows as the conversation archive arrives. */
function hydrateFleetChildren(
  fleets: readonly FleetProjection[],
  subagents: ReadonlyMap<string, SubagentEntry>,
): readonly FleetProjection[] {
  return fleets.map((fleet) => {
    if (fleet.children.length > 0 || fleet.taskIds.length === 0) return fleet;
    const children = fleet.taskIds.flatMap((taskId) => {
      const child = subagents.get(taskId);
      return child === undefined ? [] : [child];
    });
    return children.length > 0 ? { ...fleet, children } : fleet;
  });
}

function effectStageUri(stageId: string): string {
  return `${EFFECT_STAGE_URI_PREFIX}${encodeURIComponent(stageId)}`;
}

function effectStageIdForUri(uri: string): string | null {
  if (!uri.startsWith(EFFECT_STAGE_URI_PREFIX)) return null;
  const encoded = uri.slice(EFFECT_STAGE_URI_PREFIX.length);
  if (encoded === "") return null;
  try {
    const stageId = decodeURIComponent(encoded);
    return stageId === "" ? null : stageId;
  } catch {
    return null;
  }
}

function receiptV2Uri(runId: string): string {
  return `${RECEIPT_V2_URI_PREFIX}${encodeURIComponent(runId)}`;
}

function isReceiptV2Uri(uri: string, runId?: string | null): boolean {
  if (!uri.startsWith(RECEIPT_V2_URI_PREFIX)) return false;
  if (runId === undefined) return true;
  try {
    return (
      decodeURIComponent(uri.slice(RECEIPT_V2_URI_PREFIX.length)) === runId
    );
  } catch {
    return false;
  }
}

function artifactKindForRendererHint(hint: string | null): ArtifactKind | null {
  switch (hint) {
    case "artifact-code":
      return "code";
    case "artifact-document":
      return "document";
    case "artifact-dataset":
      return "dataset";
    case "artifact-file":
      return "file";
    default:
      return null;
  }
}

/**
 * A deliberately opened source artifact is navigation state, not provenance
 * payload. It keeps only the already re-authorized logical identity needed to
 * form an artifact URI; opaque source refs, physical paths, bodies, and raw
 * tool arguments never enter the canvas state.
 */
interface ExplicitArtifactTab {
  readonly kind: ArtifactKind;
  readonly artifactId: string;
  readonly revision: number;
}

function explicitArtifactTabUri(tab: ExplicitArtifactTab): string {
  return artifactUri(tab.kind, tab.artifactId, tab.revision);
}

function explicitArtifactTabTitle(tab: ExplicitArtifactTab): string {
  switch (tab.kind) {
    case "code":
      return `Code artifact · r${tab.revision}`;
    case "document":
      return `Document artifact · r${tab.revision}`;
    case "dataset":
      return `Dataset artifact · r${tab.revision}`;
    case "file":
      return `File artifact · r${tab.revision}`;
  }
}

function isMatchingDesktopWorkspaceDecision(
  value: unknown,
  snapshot: WorkspaceApprovalSnapshot,
  decision: "approve" | "reject",
): boolean {
  const result = plainRecord(value);
  if (result === null) return false;
  const expectedStatus = decision === "approve" ? "approved" : "rejected";
  return (
    result.stageId === snapshot.stageId &&
    result.revision === snapshot.revision &&
    result.decision === decision &&
    (result.status === expectedStatus || result.status === "cancelled")
  );
}

function isMatchingWebWorkspaceDecision(
  value: unknown,
  snapshot: WorkspaceApprovalSnapshot,
  decision: "approve" | "reject",
): boolean {
  const receipt = plainRecord(value);
  if (receipt === null) return false;
  return (
    receipt.stage_id === snapshot.stageId &&
    receipt.revision === snapshot.revision &&
    receipt.decision === decision &&
    receipt.proposal_digest === snapshot.proposalDigest &&
    receipt.target_digest === snapshot.targetDigest &&
    receipt.status === (decision === "approve" ? "approved" : "rejected") &&
    typeof receipt.decision_ledger_id === "string" &&
    receipt.decision_ledger_id.length > 0 &&
    typeof receipt.change_set_digest === "string" &&
    receipt.change_set_digest.length > 0
  );
}

function isMatchingMcpEffectDecision(
  value: unknown,
  review: McpEffectStageReview,
  decision: "approve" | "reject",
): boolean {
  const result = plainRecord(value);
  if (result === null) return false;
  return (
    result.stage_id === review.stageId &&
    result.revision === review.revision &&
    result.decision === decision &&
    result.proposal_digest === review.proposalDigest &&
    result.target_digest === review.targetDigest &&
    result.status === (decision === "approve" ? "approved" : "rejected") &&
    typeof result.decision_ledger_id === "string" &&
    result.decision_ledger_id.length > 0
  );
}

function plainRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function workspaceStageActionMessage(
  review: WorkspaceStageReview,
  host: WorkspaceStageHost,
  message: string | null,
): string | null {
  if (message !== null) return message;
  if (review.snapshot === null) {
    return review.stage.status === "rejected"
      ? "This rejected revision cannot be restored through the canonical workspace API. Create a new artifact revision to propose another change."
      : "This stage is incomplete, stale, or governed by policy. No workspace change can be approved.";
  }
  if (host.kind === "web") {
    return "This browser can record the reviewed decision, but it cannot write to your local workspace.";
  }
  return null;
}

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
/** Re-authorized source opens are bounded, ephemeral navigation entries. */
const MAX_EXPLICIT_ARTIFACT_TABS = 4;

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
   * A connector the host just observed finish OAuth, or `null`. Web supplies
   * `completedMcpAuthAction.serverId`: `beginAuth` full-page-redirects, so the
   * cockpit is torn down mid-flow and cannot see the return itself — without
   * this the card would come back reading `pending` on a connector that is now
   * connected. Desktop has no launcher yet, so it passes nothing and the gate
   * stays inert exactly as before.
   */
  readonly connectedConnectorServerId?: string | null;
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
  /** B2: host-owned exact-byte save path for artifact downloads. */
  readonly artifactDownloadPort?: ArtifactDownloadPort;
  /**
   * C3's narrow workspace authority seam. Desktop receives only the
   * digest-pinned Electron-main approval port; web deliberately has no local
   * write authority and uses the canonical facade decision route instead.
   * Omitted preserves the existing generic effect-stage rendering exactly.
   */
  readonly workspaceStageHost?: WorkspaceStageHost;
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
    connectedConnectorServerId = null,
    markdownComponents,
    onOrdinalSelect,
    onSelectSource,
    onJumpToChatSource,
    SourceRowComponent,
    surfacesV2 = false,
    onCopyText,
    onSaveFile,
    artifactDownloadPort,
    workspaceStageHost,
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
    setReceiptV2Opened(false);
    setExplicitArtifactTabs(EMPTY_EXPLICIT_ARTIFACT_TABS);
    setOpeningSourceId(null);
    setSourceOpenMessage(null);
    sourceOpenTokenRef.current += 1;
    // PRD-09c: never carry an open edit overlay across conversations.
    setEditingDiffId(null);
    // Surfaces v2: a new conversation starts from a clean gate/toast state.
    setGatePolicies(EMPTY_GATE_POLICIES);
    setEffectStageBusyId(null);
    setEffectStageMessages(new Map());
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
  // WS-F: the Focus Run-details panel's collapse state, persisted per
  // conversation (mirrors `useRunMode`) so it restores on reopen.
  const {
    collapsed: focusPanelCollapsed,
    setCollapsed: setFocusPanelCollapsed,
  } = useRunPanelCollapsed({ conversationId });
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
  // E1 D4: receipt-v2 is never a lifecycle-created canvas tab. It exists only
  // after a deliberate user action, while the terminal/zero-op fold remains
  // available in Focus chat as a non-opening accountability surface.
  const [receiptV2Opened, setReceiptV2Opened] = useState(false);
  // E1 D5: a successful source-open becomes a bounded, ephemeral tab even
  // when lifecycle has no canvas subject for that artifact. This stores only
  // re-authorized logical identity — never provenance references or content.
  const [explicitArtifactTabs, setExplicitArtifactTabs] = useState<
    readonly ExplicitArtifactTab[]
  >(EMPTY_EXPLICIT_ARTIFACT_TABS);
  // E1 D5: source-open replies are tied to a monotonic token so an in-flight
  // request from a prior run cannot pin a target into the newly selected run.
  const [openingSourceId, setOpeningSourceId] = useState<string | null>(null);
  const [sourceOpenMessage, setSourceOpenMessage] = useState<string | null>(
    null,
  );
  const sourceOpenTokenRef = useRef(0);

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
  // C3: only local UI progress/error for the narrow digest-pinned decision
  // handoff. No optimistic stage status is ever stored here; SSE/replay remains
  // the authority for approved/rejected/applied state.
  const [workspaceStageBusyId, setWorkspaceStageBusyId] = useState<
    string | null
  >(null);
  const [workspaceStageMessages, setWorkspaceStageMessages] = useState<
    ReadonlyMap<string, string>
  >(() => new Map());
  // Generic MCP stages are server-authorised through the same A4/A5 pipeline
  // as workspace stages. Keep only transient UI progress here; the ledger is
  // still the sole authority for decision/execution state.
  const [effectStageBusyId, setEffectStageBusyId] = useState<string | null>(
    null,
  );
  const [effectStageMessages, setEffectStageMessages] = useState<
    ReadonlyMap<string, string>
  >(() => new Map());
  // E2/F3: a monotonic nonce the "N waiting" counter chip bumps to command the
  // rail onto the Approvals tab (one-directional; the rail reacts to increases).
  const [approvalsFocusSignal, setApprovalsFocusSignal] = useState(0);
  // E1 D6: a Review action from the canonical cross-run list carries an opaque
  // run/subject target. Effects resolve onto the destination run's existing
  // lifecycle URI after its stream binds; gates intentionally have no canvas
  // payload and land in Studio's existing gate region instead.
  const [pendingWorkV2Review, setPendingWorkV2Review] =
    useState<PendingWorkCardV2 | null>(null);
  // B3: the surface whose effective tier just upgraded generic → shaped (drives
  // the non-modal ViewUpgradeToast). `null` = no pending upgrade toast.
  const [upgradedSurface, setUpgradedSurface] = useState<{
    readonly surfaceId: string;
    readonly ledgerId: string;
  } | null>(null);
  // B3: the last effective tier seen per surface — the generic→shaped edge
  // detector for the toast. A ref (not state) so it never triggers a re-render.
  const prevTierRef = useRef<Map<string, LedgerViewTier>>(new Map());

  useEffect(() => {
    setWorkspaceStageBusyId(null);
    setWorkspaceStageMessages(new Map());
    setEffectStageBusyId(null);
    setEffectStageMessages(new Map());
    setReceiptV2Opened(false);
    setExplicitArtifactTabs(EMPTY_EXPLICIT_ARTIFACT_TABS);
    setOpeningSourceId(null);
    setSourceOpenMessage(null);
    sourceOpenTokenRef.current += 1;
  }, [session.runId]);

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
    if (isReceiptV2Uri(uri)) {
      setReceiptV2Opened(false);
    }
    if (parseArtifactSurfaceUri(uri) !== null) {
      setExplicitArtifactTabs((previous) => {
        const next = previous.filter(
          (tab) => explicitArtifactTabUri(tab) !== uri,
        );
        return next.length === previous.length ? previous : next;
      });
    }
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
      setReceiptV2Opened(false);
      setExplicitArtifactTabs(EMPTY_EXPLICIT_ARTIFACT_TABS);
      setOpeningSourceId(null);
      setSourceOpenMessage(null);
      sourceOpenTokenRef.current += 1;
      // PRD-09c: rebinding the cockpit to another run closes any open overlay.
      setEditingDiffId(null);
      // Surfaces v2: a run switch resets the gate/toast state too.
      setGatePolicies(EMPTY_GATE_POLICIES);
      setEffectStageBusyId(null);
      setEffectStageMessages(new Map());
      setPendingWorkV2Review(null);
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
  const conversationSubagents = useConversationSubagentArchive(
    transport,
    conversationId,
    subagentProjection.subagents,
  );
  const conversationFleets = useConversationFleetArchive(
    transport,
    conversationId,
    session.runs.map((run) => run.runId),
    subagentProjection.fleets,
  );
  const transcriptFleets = useMemo(
    () =>
      hydrateFleetChildren(
        conversationFleets.fleets,
        conversationSubagents.subagents,
      ),
    [conversationFleets.fleets, conversationSubagents.subagents],
  );

  // The detailed tool/reasoning timeline is another pure view of the same
  // canonical event array. It is injected into both places a user can expand a
  // child (the inline fleet row and the Agents tab); it opens no stream and
  // keeps those two renderings in lockstep.
  const subagentActivityProjection = useMemo(
    () => projectSubagentActivities(session.events),
    [session.events],
  );

  // The live main-agent tool cards stay a pure projection of the active event
  // tail. The conversation archive below augments that projection with
  // completed cards from prior runs, so starting a later turn cannot erase a
  // visible `web_search` result. Subagent tool calls are excluded upstream (they
  // belong to the Agents views).
  const toolCalls = useMemo(
    () => projectToolCalls(session.events),
    [session.events],
  );
  const conversationToolCalls = useConversationToolCallArchive(
    transport,
    conversationId,
    session.runs.map((run) => run.runId),
    toolCalls,
  );

  // Focus exposes an honest, compact plan from the same canonical events as the
  // transcript. It never opens a second subscription and never infers future
  // work from the user's prompt.
  const focusPlan = useMemo(
    () => projectFocusPlan(session.events),
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

  // Keep the inline chat-only source card on the same canonical stream as
  // citation chips. The card itself requires `source_tool_call_id` equality,
  // so this flat list cannot attach an unrelated source to a tool result.
  const toolCallCitations = useMemo<readonly CitationSourceRef[]>(
    () => [...citationProjection.citations.values()],
    [citationProjection.citations],
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

  // `ask_a_question` resumes the harness with the ANSWER, not a bare approval:
  // the worker threads `answer` into the LangGraph resume payload, and an
  // approve with no answer resolves the interrupt as declined. An empty answer
  // is therefore a reject (the card's "Skip"), never a silent approve.
  const handleAnswer = useCallback(
    (approvalId: string, answer: QuestionAnswer): void => {
      if (answer.answer.trim() === "") {
        resolveApproval(approvalId, "rejected");
        return;
      }
      setLocalDecisions((prev) => {
        const next = new Map(prev);
        next.set(approvalId, "approved");
        return next;
      });
      void transport
        .request({
          method: "POST",
          path: `/v1/agent/approvals/${approvalId}/decision`,
          body: { decision: "approved", answer: answer.answer },
        })
        .catch(() => {
          /* optimistic: SSE `approval_resolved` reconciles the truth */
        });
    },
    [resolveApproval, transport],
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
  // E2 D3: select historic v2 presentation facts through the versioned,
  // pure compatibility reader. This is intentionally not a conversion into
  // `ledger` state: old run events remain append-only and every returned
  // surface is read-only. The v2.1 lifecycle continues to own canonical tabs.
  const legacyV2Replay = useMemo<LegacyV2ReplayProjection>(
    () =>
      surfacesV2
        ? projectLegacyV2Replay(session.events)
        : EMPTY_LEGACY_V2_REPLAY,
    [surfacesV2, session.events],
  );
  const legacyV2SurfaceBySubject = useMemo(
    () =>
      new Map(
        legacyV2Replay.surfaces
          .filter((surface) => surface.origin === "ledger")
          .map((surface) => [surface.subject_id, surface] as const),
      ),
    [legacyV2Replay],
  );
  const legacyV2StateByUri = useMemo(() => {
    const states = new Map<string, Record<string, unknown>>();
    for (const surface of legacyV2Replay.surfaces) {
      if (surface.state !== null) states.set(surface.uri, { ...surface.state });
    }
    return states;
  }, [legacyV2Replay]);
  const legacyV2ReadOnlyStream =
    legacyV2Replay.mode === "legacy_v2" || legacyV2Replay.mode === "mixed";
  // B3 owns the one client presentation truth. It folds artifacts, ordinary
  // surfaces, universal effects, gates, and terminal receipts from the durable
  // production event stream — no shape inference or legacy surface envelope.
  const canvasLifecycle = useMemo(
    () => (surfacesV2 ? projectCanvasLifecycle(session.events) : null),
    [surfacesV2, session.events],
  );
  const displayedCanvasLifecycle = useMemo(() => {
    if (!surfacesV2) return null;
    if (scrubbedSeq === null) return canvasLifecycle;
    return projectCanvasLifecycle(
      session.events.filter((event) => event.sequence_no <= scrubbedSeq),
    );
  }, [surfacesV2, canvasLifecycle, scrubbedSeq, session.events]);
  // C3: fold the same canonical event prefix as the lifecycle into a safe
  // workspace-stage review. The projection withholds unknown/stale data rather
  // than guessing a generic write surface. ThreadCanvas only mounts overrides
  // at live-now, but using the same prefix keeps tab state and compact Focus
  // cards semantically aligned while scrubbing.
  const workspaceStageReviews = useMemo(
    () =>
      !surfacesV2
        ? EMPTY_WORKSPACE_STAGE_REVIEWS
        : projectWorkspaceStageLifecycle(
            scrubbedSeq === null
              ? session.events
              : session.events.filter(
                  (event) => event.sequence_no <= scrubbedSeq,
                ),
          ),
    [surfacesV2, scrubbedSeq, session.events],
  );
  // The generic MCP projection is deliberately a peer of the workspace
  // projection. It sees the same scrubbed event prefix but never treats a
  // workspace stage as a generic effect decision.
  const mcpEffectStageReviews = useMemo(
    () =>
      !surfacesV2
        ? EMPTY_MCP_EFFECT_STAGE_REVIEWS
        : projectMcpEffectStages(
            scrubbedSeq === null
              ? session.events
              : session.events.filter(
                  (event) => event.sequence_no <= scrubbedSeq,
                ),
          ),
    [surfacesV2, scrubbedSeq, session.events],
  );
  const stageById = useMemo(() => {
    const map = new Map<string, LedgerStagedWrite>();
    if (!surfacesV2) return map;
    for (const stage of ledger.stages.values()) map.set(stage.stageId, stage);
    return map;
  }, [surfacesV2, ledger]);
  const stageBySurfaceId = useMemo(() => {
    const map = new Map<string, LedgerStagedWrite>();
    for (const stage of stageById.values()) {
      if (stage.surfaceId !== "") map.set(stage.surfaceId, stage);
    }
    return map;
  }, [stageById]);
  const v2CanvasTabs = useMemo(() => {
    const uriBySubjectKey = new Map<string, string>();
    const legacyUris = new Set<string>();
    if (displayedCanvasLifecycle === null) {
      return {
        tabs: [] as Array<{ uri: string; title: string; lastSeq: number }>,
        uriBySubjectKey,
        legacyUris,
        preferredUri: "",
      };
    }
    const tabs: Array<{ uri: string; title: string; lastSeq: number }> = [];
    const seen = new Set<string>();
    const add = (
      uri: string,
      title: string,
      lastSeq: number,
      key: string,
      legacy = false,
    ): boolean => {
      uriBySubjectKey.set(key, uri);
      if (seen.has(uri)) return false;
      seen.add(uri);
      tabs.push({ uri, title, lastSeq });
      if (legacy) legacyUris.add(uri);
      return true;
    };
    for (const subject of displayedCanvasLifecycle.tabs) {
      if (subject.kind === "artifact") {
        const kind = artifactKindForRendererHint(subject.rendererHint);
        if (kind !== null && subject.revision !== null) {
          add(
            artifactUri(kind, subject.subjectId, subject.revision),
            `${subject.title} · r${subject.revision}`,
            subject.lastSeq,
            subject.key,
          );
        }
        continue;
      }
      if (subject.kind === "surface") {
        const legacy = legacyV2SurfaceBySubject.get(subject.subjectId);
        if (legacy !== undefined) {
          add(
            legacy.uri,
            legacy.title ?? subject.title,
            Math.max(subject.lastSeq, legacy.last_sequence_no),
            subject.key,
            true,
          );
          continue;
        }
        const surface = ledger.surfaces.get(subject.subjectId);
        if (surface !== undefined) {
          add(
            tabUriForSurface(surface),
            subject.title,
            subject.lastSeq,
            subject.key,
          );
        }
        continue;
      }
      if (subject.kind === "effect") {
        const stage = stageById.get(subject.subjectId);
        // A legacy `write.staged` is historical evidence, not an active v2.1
        // effect. Its companion historic surface is already mounted above via
        // the compatibility renderer; do not create a second mutable stage
        // tab (and do not invent one if the old stream had no surface).
        if (stage !== undefined && legacyV2ReadOnlyStream) {
          const legacy = legacyV2SurfaceBySubject.get(stage.surfaceId);
          if (legacy !== undefined) {
            add(
              legacy.uri,
              legacy.title ?? subject.title,
              Math.max(subject.lastSeq, legacy.last_sequence_no),
              subject.key,
              true,
            );
          }
          continue;
        }
        const surface =
          stage === undefined
            ? undefined
            : ledger.surfaces.get(stage.surfaceId);
        add(
          surface === undefined
            ? effectStageUri(subject.subjectId)
            : tabUriForSurface(surface),
          subject.title,
          subject.lastSeq,
          subject.key,
        );
      }
    }
    // Historic presentation envelopes predate `surface.created`, so they have
    // no lifecycle subject. Append their exact old URI/state only when it does
    // not collide with a canonical tab; canonical v2.1 owns collisions.
    for (const surface of legacyV2Replay.surfaces) {
      add(
        surface.uri,
        surface.title ?? surface.uri,
        surface.last_sequence_no,
        `legacy-v2:${surface.origin}:${surface.subject_id}`,
        true,
      );
    }
    const preferredUri =
      (displayedCanvasLifecycle.activeSubjectKey === null
        ? undefined
        : uriBySubjectKey.get(displayedCanvasLifecycle.activeSubjectKey)) ??
      tabs[0]?.uri ??
      "";
    return { tabs, uriBySubjectKey, legacyUris, preferredUri };
  }, [
    displayedCanvasLifecycle,
    ledger,
    legacyV2Replay,
    legacyV2ReadOnlyStream,
    legacyV2SurfaceBySubject,
    stageById,
  ]);

  // E1 D4/D5 canonical folds over the same append-only event list. Receipt v2
  // is intentionally independent of lifecycle tabs: a terminal chat-only run
  // produces an available receipt but never creates a canvas subject by itself.
  const receiptV2Projection = useMemo(
    () =>
      surfacesV2 && session.runId !== null
        ? projectReceiptV2(session.runId, session.events, session.runStatus)
        : EMPTY_RECEIPT_V2,
    [surfacesV2, session.runId, session.events, session.runStatus],
  );
  const receiptV2Visible =
    receiptV2Projection.receipt !== null &&
    needsCockpitReceipt(receiptV2Projection.receipt);
  const sourcesV2Projection = useMemo<SourcesProjectionV2 | null>(
    () =>
      surfacesV2 && session.runId !== null
        ? projectSourcesV2(session.runId, session.events)
        : null,
    [surfacesV2, session.runId, session.events],
  );

  // E1 D6 Review is intentionally a local navigation operation. The aggregate
  // API names an authorised run + opaque subject only; it never sends a target
  // path or surface body. For an effect, the destination run's own lifecycle
  // fold supplies the stable canvas URI. For a gate, Studio's existing gate
  // region is the honest review surface.
  const handleReviewPendingWorkV2 = useCallback(
    (card: PendingWorkCardV2): void => {
      if (card.runId !== session.runId) {
        handleSelectRun(card.runId);
      }
      if (card.subjectKind === "effect") {
        setPendingWorkV2Review(card);
      }
      setMode("studio");
    },
    [handleSelectRun, session.runId, setMode],
  );

  useEffect(() => {
    if (
      !surfacesV2 ||
      pendingWorkV2Review === null ||
      pendingWorkV2Review.runId !== session.runId
    ) {
      return;
    }
    const uri = v2CanvasTabs.uriBySubjectKey.get(
      `effect:${pendingWorkV2Review.subjectId}`,
    );
    if (uri === undefined) {
      // The destination run may still be binding/replaying. Keep the intent
      // until its canonical lifecycle fold exposes the local URI.
      return;
    }
    setPinnedUri(uri);
    setPendingWorkV2Review(null);
  }, [pendingWorkV2Review, session.runId, surfacesV2, v2CanvasTabs]);

  const receiptV2Tab = useMemo(() => {
    if (
      !surfacesV2 ||
      !receiptV2Opened ||
      !receiptV2Visible ||
      session.runId === null
    ) {
      return null;
    }
    return {
      uri: receiptV2Uri(session.runId),
      title: "Run receipt",
      lastSeq: ledger.lastLedgerSeq,
    };
  }, [
    surfacesV2,
    receiptV2Opened,
    receiptV2Visible,
    session.runId,
    ledger.lastLedgerSeq,
  ]);

  // A provenance artifact has no requirement to have been presented as a
  // lifecycle canvas subject. Keep deliberate, re-authorized opens visible in
  // Studio in that case, while lifecycle remains canonical whenever it does
  // already carry the same artifact URI.
  const explicitArtifactCanvasTabs = useMemo(() => {
    if (!surfacesV2)
      return [] as Array<{ uri: string; title: string; lastSeq: number }>;
    const lifecycleUris = new Set(v2CanvasTabs.tabs.map((tab) => tab.uri));
    return explicitArtifactTabs.flatMap((tab) => {
      const uri = explicitArtifactTabUri(tab);
      return lifecycleUris.has(uri)
        ? []
        : [
            {
              uri,
              title: explicitArtifactTabTitle(tab),
              lastSeq: ledger.lastLedgerSeq,
            },
          ];
    });
  }, [
    surfacesV2,
    v2CanvasTabs.tabs,
    explicitArtifactTabs,
    ledger.lastLedgerSeq,
  ]);

  const explicitV2TabUris = useMemo(() => {
    const uris = new Set<string>();
    if (!surfacesV2) return uris;
    for (const tab of explicitArtifactTabs)
      uris.add(explicitArtifactTabUri(tab));
    if (receiptV2Tab !== null) uris.add(receiptV2Tab.uri);
    return uris;
  }, [surfacesV2, explicitArtifactTabs, receiptV2Tab]);

  // SDR §11 strictness: flag on ⇒ tabs come from B3's lifecycle fold plus
  // bounded, explicit receipt/source navigation state; flag off ⇒ the old v1
  // selector remains byte-identical. Neither source nor receipt opens itself.
  const surfaceTabList = useMemo(() => {
    if (!surfacesV2) return projectSurfaceTabs(session.events);
    return [
      ...v2CanvasTabs.tabs,
      ...explicitArtifactCanvasTabs,
      ...(receiptV2Tab === null ? [] : [receiptV2Tab]),
    ];
  }, [
    surfacesV2,
    v2CanvasTabs.tabs,
    explicitArtifactCanvasTabs,
    receiptV2Tab,
    session.events,
  ]);
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
            if (v2CanvasTabs.legacyUris.has(uri)) {
              return legacyV2StateByUri.get(uri);
            }
            const id = surfaceIdForTabUri(uri);
            return id !== null ? hydration.stateFor(id) : undefined;
          }
        : undefined,
    [surfacesV2, hydration, legacyV2StateByUri, v2CanvasTabs],
  );
  const visibleSurfaceTabs = useMemo(() => {
    const eligible = surfaceTabList.filter((tab) => !closedUris.has(tab.uri));
    // Explicit receipt/source navigation must remain visible above the normal
    // lifecycle cap. Their state is bounded (four artifact opens + one
    // receipt), so this cannot create an unbounded Studio strip.
    const explicit = eligible.filter((tab) => explicitV2TabUris.has(tab.uri));
    const lifecycle = eligible.filter((tab) => !explicitV2TabUris.has(tab.uri));
    return [
      ...lifecycle.slice(0, Math.max(0, MAX_SURFACE_TABS - explicit.length)),
      ...explicit,
    ];
  }, [surfaceTabList, closedUris, explicitV2TabUris]);
  const newestUri =
    visibleSurfaceTabs.length > 0 ? visibleSurfaceTabs[0].uri : "";
  const lifecyclePreferredUri = visibleSurfaceTabs.some(
    (tab) => tab.uri === v2CanvasTabs.preferredUri,
  )
    ? v2CanvasTabs.preferredUri
    : newestUri;

  // `activeUri` derivation (scrub wins → pin wins → a pending diff pulls focus →
  // else follow the newest surface). A pin only holds while its surface is still
  // on the strip, so run/conversation switches self-heal.
  const effectivePin =
    pinnedUri !== null &&
    visibleSurfaceTabs.some((tab) => tab.uri === pinnedUri)
      ? pinnedUri
      : null;
  const followDiffUri =
    !surfacesV2 && !isScrubbed && openSurfaceDiffs.length > 0
      ? openSurfaceDiffs[0].uri
      : undefined;
  const scrubTargetUri =
    scrubbedSeq !== null ? scrubIndex.get(scrubbedSeq)?.surfaceUri : undefined;
  const activeUri =
    !surfacesV2 &&
    scrubbedSeq !== null &&
    scrubTargetUri !== undefined &&
    scrubTargetUri !== ""
      ? scrubTargetUri
      : (effectivePin ??
        (surfacesV2 ? lifecyclePreferredUri : (followDiffUri ?? newestUri)));

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
    if (v2CanvasTabs.legacyUris.has(activeUri)) return null;
    const id = surfaceIdForTabUri(activeUri);
    if (id === null) return null;
    return ledger.surfaces.get(id)?.viewState ?? null;
  }, [surfacesV2, activeUri, ledger, v2CanvasTabs]);

  // PRD-B4: the active surface's folded "Suggest a shape" state (idle by default).
  const activeShapeRequest = useMemo<LedgerShapeRequestState>(() => {
    if (!surfacesV2) return "idle";
    if (v2CanvasTabs.legacyUris.has(activeUri)) return "idle";
    const id = surfaceIdForTabUri(activeUri);
    if (id === null) return "idle";
    return ledger.surfaces.get(id)?.shapeRequest ?? "idle";
  }, [surfacesV2, activeUri, ledger, v2CanvasTabs]);

  // ============================================================
  // Generative Surfaces v2 — integration mount pass
  // ============================================================
  // Every projection/hook below is a pure PEER of the ledger fold over the SAME
  // `session.events` (the one-projector invariant, FR-3.3) or one Transport-fed
  // fetch. All are gated on `surfacesV2` — flag off ⇒ empties/inert, so the
  // cockpit is byte-identical to today (memos hold a stable empty reference; the
  // pending-work hook is `enabled: false`, issuing no request).

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
  // Canonical v2.1 queue: Studio-only and independently identity-authorised by
  // the endpoint. Focus deliberately does not fetch or mount its expanded
  // cross-run list; the compact Focus layout remains unchanged.
  const pendingWorkV21 = usePendingWorkV2(
    transport,
    surfacesV2 && enabled && mode === "studio" && session.runId !== null,
    session.runId,
    ledger.lastLedgerSeq,
  );

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

  const setWorkspaceStageMessage = useCallback(
    (stageId: string, message: string | null): void => {
      setWorkspaceStageMessages((previous) => {
        const next = new Map(previous);
        if (message === null) next.delete(stageId);
        else next.set(stageId, message);
        return next;
      });
    },
    [],
  );

  // C3 D6/D8: decide only the current digest-pinned snapshot. Desktop calls
  // its narrow Electron-main bridge; web uses the canonical facade route and
  // deliberately does not obtain or claim any local workspace capability.
  const handleWorkspaceDecision = useCallback(
    (
      stageId: string,
      revision: number,
      decision: "approve" | "reject",
    ): void => {
      const review = workspaceStageReviews.get(stageId);
      const snapshot = review?.snapshot ?? null;
      if (
        snapshot === null ||
        snapshot.revision !== revision ||
        snapshot.runId !== session.runId
      ) {
        setWorkspaceStageMessage(
          stageId,
          "This stage changed before the decision could be recorded. Review the current revision.",
        );
        return;
      }
      if (workspaceStageHost === undefined) {
        setWorkspaceStageMessage(
          stageId,
          "Workspace approval is unavailable in this host. No workspace change was made.",
        );
        return;
      }

      const expectedStatus = decision === "approve" ? "approved" : "rejected";
      setWorkspaceStageBusyId(stageId);
      setWorkspaceStageMessage(stageId, null);
      void (async () => {
        try {
          if (workspaceStageHost.kind === "desktop") {
            const result = await workspaceStageHost.approvalPort.decide({
              snapshot,
              decision,
            });
            if (
              !isMatchingDesktopWorkspaceDecision(result, snapshot, decision)
            ) {
              throw new Error(
                "workspace approval host returned an invalid result",
              );
            }
            if (result.status === "cancelled") {
              setWorkspaceStageMessage(
                stageId,
                "Native confirmation was cancelled. No workspace change was made.",
              );
              return;
            }
          } else {
            const receipt = await transport.request<unknown>({
              method: "POST",
              path: `/v1/agent/effect-stages/${encodeURIComponent(
                snapshot.stageId,
              )}/decisions?run_id=${encodeURIComponent(snapshot.runId)}`,
              body: {
                revision: snapshot.revision,
                decision,
                proposal_digest: snapshot.proposalDigest,
                target_digest: snapshot.targetDigest,
              },
            });
            if (!isMatchingWebWorkspaceDecision(receipt, snapshot, decision)) {
              throw new Error(
                "workspace approval receipt did not match the stage",
              );
            }
          }
          setWorkspaceStageMessage(
            stageId,
            `Decision recorded as ${expectedStatus}. Waiting for the run ledger.`,
          );
        } catch {
          setWorkspaceStageMessage(
            stageId,
            "The workspace decision was not recorded. No workspace change was made.",
          );
        } finally {
          setWorkspaceStageBusyId((current) =>
            current === stageId ? null : current,
          );
        }
      })();
    },
    [
      session.runId,
      setWorkspaceStageMessage,
      transport,
      workspaceStageHost,
      workspaceStageReviews,
    ],
  );

  const setEffectStageMessage = useCallback(
    (stageId: string, message: string | null): void => {
      setEffectStageMessages((previous) => {
        const next = new Map(previous);
        if (message === null) next.delete(stageId);
        else next.set(stageId, message);
        return next;
      });
    },
    [],
  );

  // Standard external effects deliberately use the production A4 decision
  // route. There is no client-side executor or draft-only approval exception:
  // the server verifies this exact snapshot, then the existing A5 queue owns
  // execution. SSE/replay remains the only source of terminal stage state.
  const handleMcpEffectDecision = useCallback(
    (review: McpEffectStageReview, decision: "approve" | "reject"): void => {
      if (session.runId === null || session.runId === "") return;
      const current = mcpEffectStageReviews.get(review.stageId);
      if (
        current === undefined ||
        current.revision !== review.revision ||
        current.proposalDigest !== review.proposalDigest ||
        current.targetDigest !== review.targetDigest ||
        !current.canDecide
      ) {
        setEffectStageMessage(
          review.stageId,
          "This change was updated before the decision could be recorded. Review the current revision.",
        );
        return;
      }
      setEffectStageBusyId(review.stageId);
      setEffectStageMessage(review.stageId, null);
      void transport
        .request<unknown>({
          method: "POST",
          path: `/v1/agent/effect-stages/${encodeURIComponent(
            review.stageId,
          )}/decision?run_id=${encodeURIComponent(session.runId)}`,
          body: {
            revision: review.revision,
            decision,
            proposal_digest: review.proposalDigest,
            target_digest: review.targetDigest,
          },
        })
        .then((result) => {
          if (!isMatchingMcpEffectDecision(result, review, decision)) {
            throw new Error("effect decision response did not match the stage");
          }
          setEffectStageMessage(
            review.stageId,
            "Decision recorded. Waiting for the run ledger.",
          );
        })
        .catch(() => {
          setEffectStageMessage(
            review.stageId,
            "The decision was not recorded. The proposed change remains held.",
          );
        })
        .finally(() => {
          setEffectStageBusyId((currentId) =>
            currentId === review.stageId ? null : currentId,
          );
        });
    },
    [mcpEffectStageReviews, session.runId, setEffectStageMessage, transport],
  );

  // A staged artifact is the only edit affordance available at this product
  // boundary. Opening it creates an ordinary artifact-edit flow; it never
  // mutates a workspace stage or invents a legacy stage-revision request.
  const handleWorkspaceArtifactEdit = useCallback(
    (stageId: string): void => {
      const fallback = workspaceStageReviews.get(stageId)?.artifactFallback;
      if (fallback === null || fallback === undefined) {
        setWorkspaceStageMessage(
          stageId,
          "This stage has no editable artifact. Create a new workspace proposal to continue.",
        );
        return;
      }
      setPinnedUri(
        artifactUri(fallback.kind, fallback.artifactId, fallback.revision),
      );
      setWorkspaceStageMessage(stageId, null);
    },
    [setWorkspaceStageMessage, workspaceStageReviews],
  );

  // Web's only local-file outcome is an explicit browser download. It uses the
  // existing Transport + ArtifactDownloadPort pair and never says that a
  // workspace write happened. Desktop may still open/edit the artifact, but
  // this download fallback is intentionally offered only by the web host.
  const handleWorkspaceArtifactDownload = useCallback(
    (stageId: string): void => {
      const fallback = workspaceStageReviews.get(stageId)?.artifactFallback;
      if (
        fallback === null ||
        fallback === undefined ||
        artifactDownloadPort === undefined ||
        !isArtifactTransport(transport)
      ) {
        setWorkspaceStageMessage(
          stageId,
          "The artifact download is unavailable. No workspace change was made.",
        );
        return;
      }
      setWorkspaceStageBusyId(stageId);
      setWorkspaceStageMessage(stageId, null);
      void transport
        .getArtifactContent({
          artifactId: fallback.artifactId,
          revision: fallback.revision,
        })
        .then((content) =>
          artifactDownloadPort.saveArtifact({
            filename:
              content.filename ?? `workspace-artifact-r${fallback.revision}`,
            contentType: content.contentType,
            body: content.body,
          }),
        )
        .then(() => {
          setWorkspaceStageMessage(
            stageId,
            "Artifact downloaded. No local workspace change was made.",
          );
        })
        .catch(() => {
          setWorkspaceStageMessage(
            stageId,
            "The artifact could not be downloaded. No workspace change was made.",
          );
        })
        .finally(() => {
          setWorkspaceStageBusyId((current) =>
            current === stageId ? null : current,
          );
        });
    },
    [
      artifactDownloadPort,
      setWorkspaceStageMessage,
      transport,
      workspaceStageReviews,
    ],
  );

  // C2 gate callbacks. Connect / Skip fire the host `McpAuthPort` (the SAME
  // mid-run OAuth launcher the in-chat `mcp_auth` card uses); absent → inert but
  // visible (desktop has no mid-run launcher wired yet). The write-policy choice
  // is held locally (controlled radio) AND best-effort PATCHed to the connector.
  // The consent card's four states. `useConnectorConsentStates` wraps the host
  // port so `beginAuth`/`skipAuth` move the card, and exposes `markConnected`
  // for the one transition only the host's OAuth return can observe. Everything
  // downstream takes the WRAPPED port — handing out the original would let the
  // card's actions bypass the machine.
  const connectorConsent = useConnectorConsentStates(
    mcpAuthPort,
    connectedConnectorServerId,
  );
  const consentPort = connectorConsent.port;

  const handleGateConnect = useCallback(
    (serverId: string): void => {
      consentPort?.beginAuth(serverId);
    },
    [consentPort],
  );
  const handleGateSkip = useCallback(
    (serverId: string): void => {
      consentPort?.skipAuth(serverId);
    },
    [consentPort],
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

  // E1 D4 selection rule: receipt v2 is never auto-opened. It is opened only
  // from Studio; Focus deliberately remains chat + activity, so a terminal
  // receipt cannot occupy its review-card stack or steal the canvas.
  const handleOpenReceiptV2 = useCallback((): void => {
    if (!surfacesV2 || !receiptV2Visible || session.runId === null) {
      return;
    }
    const uri = receiptV2Uri(session.runId);
    setReceiptV2Opened(true);
    setClosedUris((previous) => {
      if (!previous.has(uri)) return previous;
      const next = new Set(previous);
      next.delete(uri);
      return next;
    });
    setPinnedUri(uri);
    setMode("studio");
  }, [surfacesV2, receiptV2Visible, session.runId, setMode]);

  // E1 D5: only the opaque source id crosses the UI boundary. The facade route
  // rechecks the run owner, refolds canonical provenance, and asks the artifact
  // owner to authorize the exact revision before a logical tab URI is formed.
  const handleOpenSourceV2 = useCallback(
    (sourceId: string): void => {
      const runId = session.runId;
      if (!surfacesV2 || runId === null || sourceId === "") return;
      const token = sourceOpenTokenRef.current + 1;
      sourceOpenTokenRef.current = token;
      setOpeningSourceId(sourceId);
      setSourceOpenMessage(null);
      void transport
        .request<unknown>({
          method: "POST",
          path: `/v1/agent/runs/${encodeURIComponent(
            runId,
          )}/sources/${encodeURIComponent(sourceId)}/open`,
          body: {},
        })
        .then((response) => {
          if (sourceOpenTokenRef.current !== token) return;
          if (
            !isSourceOpenResultV2(response) ||
            response.source_id !== sourceId ||
            response.disposition !== "artifact" ||
            response.artifact_id === null ||
            response.artifact_revision === null ||
            response.artifact_kind === null
          ) {
            setSourceOpenMessage(SOURCE_OPEN_UNAVAILABLE);
            return;
          }
          const uri = artifactUri(
            response.artifact_kind,
            response.artifact_id,
            response.artifact_revision,
          );
          const openedArtifact: ExplicitArtifactTab = {
            kind: response.artifact_kind,
            artifactId: response.artifact_id,
            revision: response.artifact_revision,
          };
          setExplicitArtifactTabs((previous) =>
            [
              openedArtifact,
              ...previous.filter((tab) => explicitArtifactTabUri(tab) !== uri),
            ].slice(0, MAX_EXPLICIT_ARTIFACT_TABS),
          );
          setClosedUris((previous) => {
            if (!previous.has(uri)) return previous;
            const next = new Set(previous);
            next.delete(uri);
            return next;
          });
          setPinnedUri(uri);
          setMode("studio");
        })
        .catch(() => {
          if (sourceOpenTokenRef.current === token) {
            setSourceOpenMessage(SOURCE_OPEN_UNAVAILABLE);
          }
        })
        .finally(() => {
          if (sourceOpenTokenRef.current === token) {
            setOpeningSourceId(null);
          }
        });
    },
    [surfacesV2, session.runId, transport, setMode],
  );

  // The kind-specific v2 surface for the active tab, injected into ThreadCanvas
  // (`renderSurfaceOverride`). Staged writes render their draft/table surface
  // (approve/apply bars composed inside); a receipt surface renders the fold.
  // `null` ⇒ ThreadCanvas takes its default v2 mount (record/message/table via
  // the pure adapter registry). Only meaningful on the v2 path.
  const renderV2Surface = useCallback(
    (uri: string): ReactNode => {
      if (uri === "" && displayedCanvasLifecycle !== null) {
        return (
          <CanvasLifecyclePanel
            lifecycle={displayedCanvasLifecycle.lifecycle}
            failure={displayedCanvasLifecycle.failure}
            onRetry={session.retry}
          />
        );
      }
      if (
        isReceiptV2Uri(uri, session.runId) &&
        receiptV2Visible &&
        receiptV2Projection.receipt !== null
      ) {
        return <ReceiptV2Surface receipt={receiptV2Projection.receipt} />;
      }
      if (parseArtifactSurfaceUri(uri) !== null) {
        return (
          <ArtifactSurface
            uri={uri}
            transport={transport}
            downloadPort={artifactDownloadPort}
          />
        );
      }
      // E2 D3 compatibility tabs intentionally fall through to the existing
      // fixed renderer registry. They are historic read-only subjects, never
      // current stages/effects, even if an old URI happens to resemble a v2
      // surface URI.
      if (v2CanvasTabs.legacyUris.has(uri)) return null;
      const effectStageId = effectStageIdForUri(uri);
      if (effectStageId !== null) {
        const workspaceReview = workspaceStageReviews.get(effectStageId);
        if (workspaceReview !== undefined && workspaceStageHost !== undefined) {
          const artifactFallback = workspaceReview.artifactFallback;
          const canDownloadArtifact =
            workspaceStageHost?.kind === "web" &&
            artifactFallback !== null &&
            artifactDownloadPort !== undefined;
          return (
            <TcWorkspaceStageSurface
              stage={workspaceReview.stage}
              busy={workspaceStageBusyId === effectStageId}
              onApprove={(stageId, revision) =>
                handleWorkspaceDecision(stageId, revision, "approve")
              }
              onReject={(stageId, revision) =>
                handleWorkspaceDecision(stageId, revision, "reject")
              }
              onEdit={
                artifactFallback === null
                  ? undefined
                  : (stageId) => handleWorkspaceArtifactEdit(stageId)
              }
              editLabel={
                artifactFallback === null ? undefined : "Edit artifact"
              }
              onDownloadArtifact={
                canDownloadArtifact
                  ? () => handleWorkspaceArtifactDownload(effectStageId)
                  : undefined
              }
              actionUnavailable={workspaceStageActionMessage(
                workspaceReview,
                workspaceStageHost,
                workspaceStageMessages.get(effectStageId) ?? null,
              )}
            />
          );
        }
        const subject = displayedCanvasLifecycle?.tabs.find(
          (item) => item.kind === "effect" && item.subjectId === effectStageId,
        );
        const mcpReview = mcpEffectStageReviews.get(effectStageId);
        return (
          <EffectStageCard
            stageId={effectStageId}
            title={mcpReview?.title ?? subject?.title ?? "Proposed change"}
            review={mcpReview}
            busy={effectStageBusyId === effectStageId}
            message={effectStageMessages.get(effectStageId) ?? null}
            onDecision={
              mcpReview === undefined
                ? undefined
                : (review, decision) =>
                    handleMcpEffectDecision(review, decision)
            }
          />
        );
      }
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
      return null;
    },
    [
      stageBySurfaceId,
      hydration,
      displayedCanvasLifecycle,
      session.retry,
      session.runId,
      receiptV2Projection.receipt,
      receiptV2Visible,
      handleRowDecision,
      handleStageApply,
      handleStageEdit,
      handleStageApprove,
      handleStageReject,
      handleStageRestore,
      transport,
      artifactDownloadPort,
      workspaceStageReviews,
      workspaceStageHost,
      workspaceStageBusyId,
      workspaceStageMessages,
      mcpEffectStageReviews,
      effectStageBusyId,
      effectStageMessages,
      handleMcpEffectDecision,
      handleWorkspaceDecision,
      handleWorkspaceArtifactEdit,
      handleWorkspaceArtifactDownload,
      v2CanvasTabs,
    ],
  );

  // Focus uses the identical lifecycle fold as Studio, but only its compact
  // cards. Opening a card selects its stable subject URI then switches mode;
  // no Focus path mounts the full canvas renderer.
  const handleOpenLifecycleSubject = useCallback(
    (subjectKey: string): void => {
      const uri = v2CanvasTabs.uriBySubjectKey.get(subjectKey);
      if (uri === undefined) return;
      setPinnedUri(uri);
      setMode("studio");
    },
    [v2CanvasTabs, setMode],
  );
  const focusCards =
    surfacesV2 && displayedCanvasLifecycle !== null ? (
      <CanvasFocusCards
        projection={displayedCanvasLifecycle}
        onOpenSubject={handleOpenLifecycleSubject}
      />
    ) : undefined;

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
  // E1 D6's canonical list is additive during migration. Only pass it after a
  // verified non-empty response or an explicit partial-result marker:
  // absent/404/cohort-off paths preserve the legacy rail DOM exactly and never
  // claim that an unavailable queue is empty.
  const railPendingWorkV21 =
    surfacesV2 &&
    mode === "studio" &&
    (pendingWorkV21.cards.length > 0 || pendingWorkV21.hasOmittedRuns)
      ? {
          cards: pendingWorkV21.cards,
          loading: pendingWorkV21.status === "loading",
          partial: pendingWorkV21.hasOmittedRuns,
          stale: pendingWorkV21.status === "error",
          hasMore: pendingWorkV21.hasMore,
          onReview: handleReviewPendingWorkV2,
          onLoadMore: pendingWorkV21.loadMore,
        }
      : undefined;

  // E1 D5's canonical, redacted source projection. It is built from the same
  // event array as the canvas and only delegates opaque source ids to the
  // owner-routed facade open flow. Absent when v2 is off, preserving the legacy
  // rail DOM and host wiring unchanged.
  const railSourcesV2 =
    surfacesV2 && sourcesV2Projection !== null
      ? {
          projection: sourcesV2Projection,
          onOpenSource: handleOpenSourceV2,
          openingSourceId,
          openMessage: sourceOpenMessage,
        }
      : undefined;

  // The pending diff handed to the center pane — ONLY for the active surface,
  // and never while scrubbed off-now (FR-3.15). It clears prop-driven: once the
  // diff resolves (optimistic or server), it drops out of `openSurfaceDiffs`, so
  // TcSurfaceMount receives `null` and hides the controls (no internal state).
  // B3 v2 subjects are declared ledger/artifact/effect identities. The retired
  // v1 envelope diff selector remains on the flag-off compatibility path only;
  // it may not hydrate or steer a v2 canvas subject.
  const activeSurfaceDiff =
    surfacesV2 || isScrubbed
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
        fleets={transcriptFleets}
        subagentActivitiesByTask={subagentActivityProjection.activitiesByTask}
        // Workstream D: inline tool-call cards, interleaved into the transcript
        // by the point each tool ran (running spinner → done/error).
        toolCalls={conversationToolCalls.toolCalls}
        toolCallCitations={toolCallCitations}
        // PR-3.10: in-chat ApprovalCard (Studio) / conf-card (Focus) + receipts.
        approvals={chatApprovals}
        onApprove={handleApprove}
        onReject={handleReject}
        onAnswer={handleAnswer}
        // WC-P5a (AD-6/AD-7): the MCP-OAuth launcher. TcChat renders the Connect
        // card (→ this port) for `mcp_auth` gates / `mcp_discovery:` suggestions
        // instead of Approve/Reject, keeping them off the `/decision` POST. Absent
        // → the card renders inert (host wires the launcher in P5b).
        mcpAuthPort={consentPort}
        connectorConsentStates={connectorConsent.states}
        onConnectorConsentCancel={connectorConsent.markPending}
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
      subagents={conversationSubagents.subagents}
      subagentsLoading={conversationSubagents.loading}
      subagentsError={conversationSubagents.error}
      subagentActivitiesByTask={subagentActivityProjection.activitiesByTask}
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
      // Surfaces v2 (E1/E2): canonical safe Sources provenance, the cross-run
      // queue + fleet, and the header chip's "jump to Approvals" signal. All
      // undefined when the flag is off ⇒ the rail is byte-identical.
      sourcesV2={railSourcesV2}
      pendingV2={railPendingV2}
      pendingWorkV21={railPendingWorkV21}
      focusApprovalsSignal={surfacesV2 ? approvalsFocusSignal : undefined}
      // WS-F: Focus Run-details panel collapse — persisted per conversation.
      panelCollapsed={focusPanelCollapsed}
      onPanelCollapsedChange={setFocusPanelCollapsed}
      focusPlan={focusPlan}
      focusActivityLive={
        session.runStatus !== null &&
        CANCELLABLE_RUN_STATUSES.has(session.runStatus)
      }
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
      // B3 Focus is a compact projection of the same lifecycle—not a hidden
      // full Studio canvas. Undefined on the v1 path preserves legacy Focus.
      focusCards={focusCards}
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
      {mode === "studio" && receiptV2Visible && !receiptV2Opened ? (
        <ReceiptV2LaunchCard
          receipt={receiptV2Projection.receipt}
          onOpen={handleOpenReceiptV2}
        />
      ) : null}
      {mode === "studio" &&
      !legacyV2ReadOnlyStream &&
      ledger.openGates.length > 0 ? (
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

  const v2HeaderStatus = surfacesV2 ? (
    <span data-testid="run-v2-chip-bar" style={v2HeaderStatusStyle}>
      <PostureChip bypassOn={ledger.bypassFromLedger} />
      <PendingCounterChip
        count={pendingWork.cards.length}
        onClick={handleOpenApprovals}
      />
    </span>
  ) : undefined;

  return (
    <div
      className="run-destination"
      data-testid="run-destination"
      data-run-status={session.status}
      data-mode={mode}
      style={rootStyle}
    >
      <RunCockpitScopeStyles />
      <RunHeader
        goal={derivedGoal}
        agentName={agentName}
        mode={mode}
        onModeChange={setMode}
        // WC-P6b: the `● working` pulse chip, derived from the single event
        // projection's run status (no second subscription — FR-3.3). Live →
        // pulses; terminal / null → absent.
        runStatus={session.runStatus}
        status={v2HeaderStatus}
      />

      {/* The selector owns the multiple-run case only. It returns null for zero
          or one run, so the idle and single-run cockpits stay chrome-free while
          every barrel-exported selector has a real in-package mount. */}
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

      <div data-testid="run-cockpit-canvas-slot" style={canvasSlotStyle}>
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

/**
 * `TcChat` and `Composer` are deliberately reusable primitives with inline
 * styles. The cockpit owns their surrounding layout, so its design-specific
 * geometry is applied here, scoped to this destination only. `!important` is
 * necessary solely to supersede the primitives' inline defaults; standalone
 * ThreadCanvas consumers remain completely unchanged.
 */
const RUN_COCKPIT_SCOPE_CSS = `
  .run-destination [data-testid="tc-chat"] {
    box-sizing: border-box !important;
    background: var(--color-bg) !important;
    border-right: 1px solid var(--color-border) !important;
    gap: normal !important;
    padding: 0 !important;
  }

  .run-destination[data-mode="focus"] [data-testid="tc-chat"] {
    margin: 0 !important;
    max-width: none !important;
  }

  .run-destination [data-testid="tc-chat-messages"] {
    gap: 14px !important;
    padding: 16px !important;
  }

  .run-destination [data-testid="composer"] {
    display: block !important;
    flex-direction: row !important;
    gap: normal !important;
  }

  .run-destination[data-mode="focus"][data-run-status="streaming"]
    [data-testid^="tc-chat-message-"]:has(.reasoning-markdown) {
      border-color: var(--color-text-muted) !important;
      color: var(--color-text-muted) !important;
      display: block !important;
      font-size: 11.5px !important;
    }
`;

function RunCockpitScopeStyles(): ReactElement {
  return (
    <style data-testid="run-cockpit-scope-styles">
      {RUN_COCKPIT_SCOPE_CSS}
    </style>
  );
}

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
  boxSizing: "border-box",
  display: "flex",
  flexDirection: "column",
  height: "100%",
  minHeight: 0,
  width: "100%",
  overflow: "hidden",
  border: "1px solid var(--color-border-strong)",
  borderRadius: 12,
  boxShadow: "0 40px 100px -30px rgba(0, 0, 0, 0.8)",
  background: "var(--color-bg)",
  color: "var(--color-text)",
  fontFamily: "var(--font-sans)",
};

const canvasSlotStyle: CSSProperties = {
  flex: 1,
  minHeight: 0,
  position: "relative",
  background: "var(--color-bg)",
};

// Surfaces v2 — the header-level write posture + pending-work controls. Keeping
// them in the existing chrome preserves discoverability without stealing height
// from the Studio canvas.
const v2HeaderStatusStyle: CSSProperties = {
  alignItems: "center",
  display: "inline-flex",
  flexShrink: 0,
  gap: 6,
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
