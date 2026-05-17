import type {
  ApprovalDecision,
  Conversation,
  CreateRunRequest,
  Message,
  ModelCatalogModel,
  RuntimeEventEnvelope,
  Skill,
  SubagentEntry,
} from "@enterprise-search/api-types";
import type {
  AppendMessage,
  CompleteAttachment,
  ThreadMessageLike,
} from "./runtime/types";
import {
  AtlasCompositeAttachmentAdapter,
  AtlasFileAttachmentAdapter,
  AtlasImageAttachmentAdapter,
  AtlasTextAttachmentAdapter,
  AtlasWebSpeechDictationAdapter,
  type ComposerHandle,
} from "./runtime";
import type { ReactElement } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  cancelRun,
  createConversation,
  createRun,
  decideApproval,
  requestApprovalUndo,
  getConversation,
  listConversations,
  listMessages,
  replayRunEvents,
  streamRunEvents,
  updateConversation,
  type AgentEventStream,
} from "../../api/agentApi";
import type { RequestIdentity } from "../../api/config";
import { updateMyPreferences } from "../../api/meApi";
import { isOAuthSetupRequired } from "../../api/mcpErrors";
import { ConnectorSuggestionCard } from "../connectors/ConnectorConsentCard";
import { ConnectorPopover } from "../connectors/ConnectorPopover";
import { McpOverlay } from "../connectors/mcp/McpOverlay";
import type { ConnectorState } from "../connectors/useConnectors";
import { useConversationConnectors } from "../connectors/useConversationConnectors";
import {
  activeCount as activeConnectorCount,
  projectChatConnectors,
} from "../connectors/projectConnectors";
import type { SkillState } from "../skills/useSkills";
import { ComposerConnectorsButton } from "./components/composer/ComposerConnectorsButton";
import {
  DetailsPanelHost,
  type DetailsPanelKind,
} from "./components/details/DetailsPanelHost";
import { Topbar } from "./components/shell";
import { SharePopover } from "../share/SharePopover";
import {
  DEFAULT_THINKING_DEPTH,
  isThinkingDepth,
  modelSupportsDepth,
  type ThinkingDepth,
} from "./depth";
import { useLocalStorageState } from "../../utils/useLocalStorageState";
import { useViewportOverlay } from "../../utils/useViewportOverlay";
import { ApprovalFocusProvider } from "./approval/ApprovalFocusContext";

type ChatSettingsTarget = "profile" | "connectors" | "skills";
import {
  applyRuntimeEvent,
  chatItemsToThreadMessages,
  markPendingInteractionsCancelled,
  messagesToChatItems,
  optimisticUserMessage,
  resolveApprovalDecision,
  resolveAuthenticatedMcpServers,
  resolveMcpAuthSkip,
  threadMessagesToChatItems,
  type ChatItem,
  type ChatThreadMessage,
} from "./chatModel";
import {
  applyCitationEvent,
  buildCitationRegistry,
} from "./chatModel/citationReducer";
import {
  applyCitationLinkEvent,
  buildCitationLinkRegistry,
  emptyCitationLinkRegistry,
  type CitationLinkRegistryByRun,
} from "./chatModel/citationLinkReducer";
import {
  citationsForRun,
  emptyCitationRegistry,
  type CitationRegistryByRun,
} from "@enterprise-search/chat-surface";
import { applySubagentEvent } from "./chatModel/subagentReducer";
import { applyDraftUpdatedEvent } from "./chatModel/draftsRegistry";
import { CitationsProvider } from "./components/citations/citationsContext";
import { useArchivedSources } from "../sources/useArchivedSources";
import { useWorkspacePaneAutoOpenSignal } from "./components/workspace/useWorkspacePaneAutoOpen";
import {
  scrollChatToCitation,
  scrollChatToEvent,
  useKeyValueStore,
} from "@enterprise-search/chat-surface";
import {
  readDepth as readDepthKv,
  writeConversationDepth,
  writeDefaultDepth,
} from "./chatDepthKv";
import { SourcePreviewProvider } from "./components/citations/SourcePreview";
import { SubagentFleetProvider } from "./components/subagents/SubagentFleetContext";
import { listSources } from "../../api/agentApi";
import {
  applySourceEvent,
  emptySourceMap,
  seedSourceMap,
  type SourceEntryMap,
} from "./chatModel/sourcesReducer";
import {
  citedToolSources,
  toolInvocationIndex,
} from "./chatModel/citedToolSources";
import { WorkspacePane } from "./components/workspace/WorkspacePane";
import { useWorkspacePaneState } from "./components/workspace/useWorkspacePaneState";
import { useSubagents } from "./components/workspace/useSubagents";
import {
  useSubagentActivities,
  useSubagentHistory,
} from "./components/workspace/useSubagentActivities";
import { useDrafts } from "./components/workspace/useDrafts";
import { useApprovalsQueue } from "./components/workspace/useApprovalsQueue";
import {
  AssistantThread,
  AssistantThreadList,
  ThreadBody,
} from "./assistantUiComponents";
import { REGENERATE_PREVIOUS_RESPONSE_PROMPT } from "./prompts";
import {
  rememberPendingMcpAuthAction,
  type CompletedMcpAuthAction,
} from "./mcpAuthAction";
import { deriveRunUiState, isRunUiEvent } from "./chatRunState";
import { hasPendingAction } from "./chatModel/status";
import { useAuth } from "../auth/AuthContext";
import { usePinnedConversations } from "./sidebar/usePinnedConversations";
import { isTerminalAssistantStatus } from "./utils/activityDataBuilders";
import { useBackgroundChatStreams } from "./runtime/useBackgroundChatStreams";
import { errorMessage } from "../../utils/errors";

type SubmitMessageOptions = {
  parentMessageId?: string | null;
  sourceMessageId?: string | null;
  branchId?: string | null;
  optimisticMessageId?: string;
};

// SSE reconnect backoff. First failure keeps the original ~750ms feel so
// transient blips recover snappily; subsequent failures double up to a 30s
// ceiling. Full-jitter (random_between(0, computed)) avoids a thundering-
// herd reconnect if many tabs lose the stream simultaneously — the
// AWS-recommended pattern.
//
// Reset point: handleEvent zeroes `reconnectAttemptsRef` when a healthy
// event arrives, so once delivery resumes the next failure starts over
// from BASE rather than continuing from the high end of the curve.
const RECONNECT_BASE_MS = 750;
const RECONNECT_MAX_MS = 30_000;

export function computeReconnectDelayMs(
  attempts: number,
  random: () => number = Math.random,
): number {
  if (attempts <= 1) return RECONNECT_BASE_MS;
  const exponential = Math.min(
    RECONNECT_BASE_MS * 2 ** (attempts - 1),
    RECONNECT_MAX_MS,
  );
  // Full jitter: spread retries across [0, exponential]. Guard with a
  // floor of BASE so we never schedule a near-zero retry that creates
  // a tight loop on a permanently-broken stream.
  return Math.max(RECONNECT_BASE_MS, Math.floor(random() * exponential));
}

export function ChatScreen({
  connectors,
  skills,
  onOpenSettings,
  identity,
  oauthStatus,
  completedMcpAuthAction,
}: {
  connectors: ConnectorState;
  skills: SkillState;
  onOpenSettings: (section?: ChatSettingsTarget) => void;
  identity: RequestIdentity;
  oauthStatus: string | null;
  completedMcpAuthAction: CompletedMcpAuthAction | null;
}): ReactElement {
  // PR 3.5 (closes PR 2.2 G4) — UserCard's WorkspacePicker forwards a
  // chosen orgId up through Sidebar → AssistantThreadList → here. We hand
  // it to the auth context, which currently hard-navs to ?workspace=<id>
  // and lets <AuthGate> re-discover the session (PR 2.2 §3.7 fallback).
  const auth = useAuth();
  // PR F3 — sidebar pin / unpin (localStorage; backend gains a typed
  // metadata.pinned column in a future PR).
  const pinned = usePinnedConversations(auth.identity?.user_id ?? null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [items, setItems] = useState<ChatItem[]>([]);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [showConnectorSuggestions, setShowConnectorSuggestions] =
    useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [detailsPanel, setDetailsPanel] = useState<DetailsPanelKind | null>(
    null,
  );
  const [status, setStatus] = useState("Ready");
  const [historyLoading, setHistoryLoading] = useState(false);
  const [initialHistoryLoaded, setInitialHistoryLoaded] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [selectedModelId, setSelectedModelId] = useState(demoModels[0].id);
  // Phase 1 P1-C (chats-canvas-prd §16) — thinking depth rides as a
  // top-level `reasoning_depth` field on `CreateRunRequest`. Persisted
  // across reloads via the existing localStorage key for backwards-
  // compatibility; the per-conversation / per-user KV persistence
  // shipped alongside (see `chatDepthKv.ts`). Mid-run depth changes
  // never affect the active run — the worker reads the frozen
  // ModelConfig from runtime_context.
  const [depthLocal, setDepthLocal] = useLocalStorageState<ThinkingDepth>(
    "atlas:thinking-depth",
    DEFAULT_THINKING_DEPTH,
    isThinkingDepth,
  );
  // KeyValueStore-backed depth persistence (chats-canvas-prd §16).
  // Resolution: per-conversation → per-user default → null (runtime
  // default). We keep `depthLocal` as the active in-render value so the
  // UI never blocks on KV reads, and sync it from KV on mount /
  // conversation switch. Writes go to both the legacy localStorage key
  // (via setDepthLocal) AND the KV store so the substrate-portable path
  // is always populated.
  const kvStore = useKeyValueStore();
  const depth = depthLocal;
  const setDepth = useCallback(
    (next: ThinkingDepth): void => {
      setDepthLocal(next);
      // Persist per-user default unconditionally; per-conversation when
      // a conversation is active. P1-B's Composer extras will surface
      // an explicit per-conv vs. per-user picker — for P1-C, "the user
      // changed depth" implies "make it the per-user default and the
      // current conversation's pick".
      writeDefaultDepth(kvStore, next);
      if (conversationId !== null) {
        writeConversationDepth(kvStore, conversationId, next);
      }
    },
    [conversationId, kvStore, setDepthLocal],
  );
  const streamRef = useRef<AgentEventStream | null>(null);
  const latestSequenceRef = useRef(0);
  const reconnectTimeoutRef = useRef<number | null>(null);
  // Tracks consecutive reconnect failures so the next delay grows
  // exponentially. Reset to 0 inside handleEvent the moment a healthy
  // event arrives — that's the proof the stream is delivering again, so
  // any subsequent failure should retry quickly, not wait minutes.
  const reconnectAttemptsRef = useRef(0);
  const activeRunUserMessageIdsRef = useRef<Map<string, string>>(new Map());
  const pendingApprovalDecisionsRef = useRef<Set<string>>(new Set());
  const latestReplaySequenceByRunRef = useRef<Map<string, number>>(new Map());
  const [latestRunEvent, setLatestRunEvent] =
    useState<RuntimeEventEnvelope | null>(null);
  // PR 1.1 — per-run citation registry. Built from `source_ingested`
  // events live during a run and from `final_response.citations` on
  // archive reads. Lives alongside `items` so the existing reducer stays
  // focused on chat content.
  const [citations, setCitations] = useState<CitationRegistryByRun>(
    emptyCitationRegistry,
  );
  // PR 1.1-rev2 — model-declared citation link registry. Built from
  // `citation_made` events as the model emits ``[[N]]`` markers.
  // Lives in parallel with `citations` during the rollout window.
  const [citationLinks, setCitationLinks] = useState<CitationLinkRegistryByRun>(
    emptyCitationLinkRegistry,
  );
  // PR 3.2 — Sources tab snapshot. Seeded from
  // `GET /v1/agent/conversations/{id}/sources` on conversation switch,
  // overlaid live by `applySourceEvent`. Conversation-scoped, not run-
  // scoped, because the Sources tab spans every run in the chat.
  const [sourcesMap, setSourcesMap] = useState<SourceEntryMap>(emptySourceMap);
  const [sourcesLoading, setSourcesLoading] = useState(false);
  const [sourcesError, setSourcesError] = useState<string | null>(null);

  // PR 2.2.1 — background runtime store. Owns per-conversation slots
  // for non-visible chats + the per-run SSE stream registry. ChatScreen
  // freezes the visible state into a slot on switch-away, thaws on
  // switch-back, and routes incoming SSE events through `routeEvent`
  // when they belong to a non-visible run.
  const bg = useBackgroundChatStreams();
  // Read the visible conv id from a ref inside the SSE callback so
  // route decisions always reflect the current visible chat (the
  // callback closure was created when the stream opened).
  const visibleConvIdRef = useRef<string | null>(null);
  visibleConvIdRef.current = conversationId;

  // PR 3.2 — Workspace pane state (open/closed + active tab + per-conv
  // manual-close memory). Topbar panel toggle, pane close button, and
  // the auto-open signal all read/write through here.
  const paneState = useWorkspacePaneState({
    conversationId,
    initialOpen: false,
    initialTab: "sources",
  });
  // PR 3.2 — workspace data feeds. Each hook owns its own archive seed
  // + cancellation; `handleEvent` overlays live deltas through the
  // setters / reducers.
  const subagentsState = useSubagents(conversationId, identity);
  const draftsState = useDrafts(conversationId, identity);
  const { setSubagents } = subagentsState;
  const { setRegistry: setDraftRegistry } = draftsState;
  const [selectedComposerSkills, setSelectedComposerSkills] = useState<Skill[]>(
    [],
  );

  useEffect(() => {
    setSelectedComposerSkills([]);
  }, [conversationId]);

  // Phase 1 P1-C (chats-canvas-prd §16) — on conversation switch, pull
  // the effective depth from KV (per-conversation → per-user default).
  // Falls back to the legacy localStorage value (already in `depthLocal`
  // via useLocalStorageState) when neither KV key is set, so existing
  // installs don't reset to balanced on first load after this lands.
  // Intentionally re-runs only on conversation switch — kvStore is
  // provider-owned (stable identity); setDepthLocal is reference-stable
  // from useState.
  useEffect(() => {
    const fromKv = readDepthKv(kvStore, conversationId);
    if (fromKv !== null) {
      setDepthLocal(fromKv);
    }
  }, [conversationId, kvStore, setDepthLocal]);

  const suggestedServers = useMemo(
    () =>
      connectors.servers.filter((server) => {
        return server.enabled && server.auth_state !== "authenticated";
      }),
    [connectors.servers],
  );

  const refreshConversations = useCallback(async (): Promise<void> => {
    const response = await listConversations(identity);
    setConversations(response.conversations);
  }, [identity]);

  // PR F3 — sidebar overflow archive. Calls the existing PR 1.6
  // `updateConversation({archived: true})` route then refreshes the
  // list so the archived row drops out (server-side filter excludes
  // archived from the default sidebar view).
  const onArchiveConversation = useCallback(
    async (id: string): Promise<void> => {
      await updateConversation(id, { archived: true }, identity);
      // Optimistic local removal so the UI reacts immediately.
      setConversations((current) =>
        current.filter((c) => c.conversation_id !== id),
      );
      // If the archived conversation was active, drop the active id so
      // the user doesn't end up viewing a hidden thread.
      if (conversationId === id) {
        setConversationId(null);
      }
      pinned.togglePinned(id, false);
    },
    [conversationId, identity, pinned],
  );

  const loadHistoryItems = useCallback(
    async (
      nextConversationId: string,
    ): Promise<{
      items: ChatItem[];
      replayFailed: boolean;
      latestSequenceByRunId: Map<string, number>;
      citations: CitationRegistryByRun;
      citationLinks: CitationLinkRegistryByRun;
    }> => {
      const history = await listMessages(nextConversationId, identity);
      const replay = await replayEventsForMessages(history.messages, identity);
      const allEvents: RuntimeEventEnvelope[] = [];
      for (const events of replay.eventsByRunId.values()) {
        for (const event of events) {
          allEvents.push(event);
        }
      }
      return {
        items: messagesToChatItems(history.messages, replay.eventsByRunId),
        replayFailed: replay.replayFailed,
        latestSequenceByRunId: latestSequenceByRunId(replay.eventsByRunId),
        citations: buildCitationRegistry(allEvents),
        // PR 1.1-rev2 — rebuild model-declared link registry on history
        // load so `[[N]]` chips render after a page reload / OAuth
        // restore / new-chat / conversation-switch, just like the
        // legacy `[c<id>]` registry.
        citationLinks: buildCitationLinkRegistry(allEvents),
      };
    },
    [identity],
  );

  useEffect(() => {
    let cancelled = false;
    async function loadInitialConversation(): Promise<void> {
      try {
        setInitialHistoryLoaded(false);
        setHistoryLoading(true);
        setStatus("Loading history...");
        const response = await listConversations(identity);
        if (cancelled) {
          return;
        }
        setConversations(response.conversations);
        const latest = response.conversations[0];
        if (!latest) {
          setConversationId(null);
          latestReplaySequenceByRunRef.current = new Map();
          setItems([]);
          setCitations(emptyCitationRegistry());
          setCitationLinks(emptyCitationLinkRegistry());
          setStatus("Ready");
          setHistoryError(null);
          setInitialHistoryLoaded(true);
          return;
        }
        setConversationId(latest.conversation_id);
        const history = await loadHistoryItems(latest.conversation_id);
        if (!cancelled) {
          latestReplaySequenceByRunRef.current = history.latestSequenceByRunId;
          setItems(history.items);
          setCitations(history.citations);
          setCitationLinks(history.citationLinks);
          setStatus(history.replayFailed ? historyReplayWarning : "Ready");
          setHistoryError(null);
        }
      } catch (err) {
        if (!cancelled) {
          const message = errorMessage(err, "Could not load chat history");
          setHistoryError(message);
          setStatus(message);
        }
      } finally {
        if (!cancelled) {
          setHistoryLoading(false);
          setInitialHistoryLoaded(true);
        }
      }
    }

    void loadInitialConversation();
    return () => {
      cancelled = true;
      if (reconnectTimeoutRef.current !== null) {
        window.clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      streamRef.current?.close();
      // PR 2.2.1 — close every backgrounded stream on auth-context
      // teardown so we don't leak EventSource connections across login
      // / workspace switches.
      bg.reset();
    };
    // bg is intentionally elided from deps — its identity is stable
    // (the hook returns a memoised object). Re-running this effect
    // because of bg would tear down the visible run on every freeze.
  }, [identity, loadHistoryItems]);

  useEffect(() => {
    // Depend on both `connectors.servers` AND `items`: history loads
    // asynchronously, often *after* `connectors.servers` settles, so a
    // single connectors-only-deps pass misses pre-existing
    // `mcp_auth_required` cards already in scrollback.
    // `resolveAuthenticatedMcpServers` is reference-stable when
    // nothing resolves, so this is loop-safe.
    setItems((current) =>
      resolveAuthenticatedMcpServers(current, connectors.servers),
    );
  }, [connectors.servers, items]);

  // PR 3.2 — seed the Sources tab snapshot on conversation switch. The
  // live source_ingested event reducer overlays subsequent events. We
  // seed in a focused effect (rather than reusing loadHistoryItems) so
  // the round-trip never blocks message history rendering.
  useEffect(() => {
    if (conversationId === null || identity === null) {
      setSourcesMap(emptySourceMap());
      setSourcesError(null);
      return undefined;
    }
    let cancelled = false;
    setSourcesLoading(true);
    setSourcesError(null);
    void listSources(conversationId, identity)
      .then((response) => {
        if (cancelled) return;
        setSourcesMap(seedSourceMap(response.sources));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setSourcesError(errorMessage(err, "Could not load sources"));
      })
      .finally(() => {
        if (!cancelled) setSourcesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId, identity]);

  const handleEvent = useCallback(
    (event: RuntimeEventEnvelope) => {
      // PR 2.2.1 — route by run owner. The stream registry maps
      // `event.run_id` → `conversationId`; if that is the visible chat,
      // apply through the visible setters (today's path); otherwise
      // hand off to the background slot so the run keeps accumulating
      // tokens / citations / sources without rendering. Subagent +
      // draft data feeds remain conversation-scoped via their own
      // hooks, so they're only updated when the run belongs to the
      // visible conversation.
      const ownerConvId = bg.conversationIdForRun(event.run_id);
      const visibleConvId = visibleConvIdRef.current;
      const isVisibleRun =
        ownerConvId === null ? true : ownerConvId === visibleConvId;
      if (!isVisibleRun) {
        bg.routeEvent(event);
        if (
          event.event_type === "run_completed" ||
          event.event_type === "run_cancelled" ||
          event.event_type === "run_failed"
        ) {
          bg.markTerminal(event.run_id, statusForTerminalRunEvent(event));
          bg.closeStream(event.run_id);
          void refreshConversations();
        }
        return;
      }
      latestSequenceRef.current = Math.max(
        latestSequenceRef.current,
        event.sequence_no,
      );
      // Stream is delivering — any failure after this should start the
      // backoff curve over rather than continuing from a high attempt count.
      reconnectAttemptsRef.current = 0;
      setItems((current) =>
        withAssistantParent(
          applyRuntimeEvent(current, event),
          event.run_id,
          activeRunUserMessageIdsRef.current.get(event.run_id) ?? null,
        ),
      );
      setCitations((current) => applyCitationEvent(current, event));
      setCitationLinks((current) => applyCitationLinkEvent(current, event));
      // PR 3.2 — Sources / Agents / Draft live overlays. Each reducer
      // is a no-op for events it doesn't recognize, so the dispatch
      // table stays flat.
      setSourcesMap((current) => applySourceEvent(current, event));
      setSubagents((current) => applySubagentEvent(current, event));
      setDraftRegistry((current) => applyDraftUpdatedEvent(current, event));
      if (isRunUiEvent(event)) {
        setLatestRunEvent(event);
      }
      if (
        event.event_type === "run_completed" ||
        event.event_type === "run_cancelled" ||
        event.event_type === "run_failed"
      ) {
        if (reconnectTimeoutRef.current !== null) {
          window.clearTimeout(reconnectTimeoutRef.current);
          reconnectTimeoutRef.current = null;
        }
        streamRef.current?.close();
        streamRef.current = null;
        bg.closeStream(event.run_id);
        setActiveRunId(null);
        activeRunUserMessageIdsRef.current.delete(event.run_id);
        setStatus(statusForTerminalRunEvent(event));
        void refreshConversations();
      }
    },
    [bg, refreshConversations, setSubagents, setDraftRegistry],
  );

  const startEventStream = useCallback(
    (runId: string, afterSequence: number, conversationIdForRun: string) => {
      if (reconnectTimeoutRef.current !== null) {
        window.clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      streamRef.current?.close();
      const stream = streamRunEvents({
        runId,
        afterSequence,
        identity,
        onEvent: handleEvent,
        onError: () => {
          streamRef.current?.close();
          streamRef.current = null;
          setStatus("Stream paused. Reconnecting...");
          reconnectAttemptsRef.current += 1;
          const delay = computeReconnectDelayMs(reconnectAttemptsRef.current);
          reconnectTimeoutRef.current = window.setTimeout(() => {
            startEventStream(
              runId,
              latestSequenceRef.current,
              conversationIdForRun,
            );
          }, delay);
        },
        onProtocolError: (error) => {
          setStatus(error.message);
        },
      });
      streamRef.current = stream;
      // Register with the background runtime store so the registry can
      // route events by run-owner and close the SSE on terminal events
      // even if the user has switched away.
      bg.registerStream({
        runId,
        conversationId: conversationIdForRun,
        stream,
      });
    },
    [bg, handleEvent, identity],
  );

  useEffect(() => {
    if (
      !initialHistoryLoaded ||
      activeRunId !== null ||
      streamRef.current !== null ||
      conversationId === null
    ) {
      return;
    }
    const pendingRunId = pendingActionRunId(items);
    if (pendingRunId === null) {
      return;
    }
    const userMessageId = userMessageIdForRun(items, pendingRunId);
    if (userMessageId) {
      activeRunUserMessageIdsRef.current.set(pendingRunId, userMessageId);
    }
    latestSequenceRef.current =
      latestReplaySequenceByRunRef.current.get(pendingRunId) ?? 0;
    setActiveRunId(pendingRunId);
    setLatestRunEvent(null);
    startEventStream(pendingRunId, latestSequenceRef.current, conversationId);
  }, [
    activeRunId,
    conversationId,
    initialHistoryLoaded,
    items,
    startEventStream,
  ]);

  useEffect(() => {
    if (
      completedMcpAuthAction === null ||
      completedMcpAuthAction.runId === null ||
      !initialHistoryLoaded
    ) {
      return;
    }
    const runId = completedMcpAuthAction.runId;

    let cancelled = false;
    async function restoreRunAfterOAuth(): Promise<void> {
      try {
        setStatus("Resuming after connector auth...");
        const replay = await replayRunEvents(runId, identity);
        if (cancelled) {
          return;
        }
        const events = [...replay.events].sort(
          (left, right) => left.sequence_no - right.sequence_no,
        );
        const latestSequence = events.reduce(
          (latest, event) => Math.max(latest, event.sequence_no),
          latestSequenceRef.current,
        );
        const latestEvent = events.at(-1);
        const latestUiEvent = events.filter(isRunUiEvent).at(-1) ?? null;
        setItems((current) => {
          const userMessageId = userMessageIdForRun(current, runId);
          if (userMessageId) {
            activeRunUserMessageIdsRef.current.set(runId, userMessageId);
          }
          return events.reduce((next, event) => {
            const parentId =
              activeRunUserMessageIdsRef.current.get(event.run_id) ??
              userMessageIdForRun(next, event.run_id);
            return withAssistantParent(
              applyRuntimeEvent(next, event),
              event.run_id,
              parentId,
            );
          }, current);
        });
        setCitations((current) =>
          events.reduce(
            (next, event) => applyCitationEvent(next, event),
            current,
          ),
        );
        setCitationLinks((current) =>
          events.reduce(
            (next, event) => applyCitationLinkEvent(next, event),
            current,
          ),
        );
        latestSequenceRef.current = latestSequence;
        if (latestEvent && isTerminalRunEvent(latestEvent)) {
          streamRef.current?.close();
          streamRef.current = null;
          setActiveRunId(null);
          setLatestRunEvent(null);
          activeRunUserMessageIdsRef.current.delete(runId);
          setStatus(statusForTerminalRunEvent(latestEvent));
          void refreshConversations();
          return;
        }
        setActiveRunId(runId);
        setLatestRunEvent(latestUiEvent);
        setStatus("Working...");
        // The MCP-OAuth resume path always runs against the visible
        // conversation (the user just clicked Connect inside it).
        if (visibleConvIdRef.current !== null) {
          startEventStream(
            runId,
            latestSequenceRef.current,
            visibleConvIdRef.current,
          );
        }
      } catch (err) {
        if (!cancelled) {
          setStatus(errorMessage(err, "Could not resume connector auth run"));
        }
      }
    }

    void restoreRunAfterOAuth();
    return () => {
      cancelled = true;
    };
  }, [
    completedMcpAuthAction,
    identity,
    initialHistoryLoaded,
    refreshConversations,
    startEventStream,
  ]);

  const loadConversationById = useCallback(
    async (nextConversationId: string): Promise<void> => {
      const previousConvId = visibleConvIdRef.current;
      if (previousConvId === nextConversationId) {
        return;
      }
      // PR 2.2.1 — freeze the outgoing visible state into the
      // background slot store so its run keeps streaming. Then either
      // thaw the target slot if we already have its state in memory
      // (warm switch — no replay round trip), or load history fresh.
      if (previousConvId !== null) {
        bg.freezeVisible({
          conversationId: previousConvId,
          snapshot: {
            items,
            citations,
            citationLinks,
            sources: sourcesMap,
            activeRunId,
            latestRunEvent,
            userMessageIdByRunId: new Map(activeRunUserMessageIdsRef.current),
            latestSequenceByRunId: new Map(
              latestReplaySequenceByRunRef.current,
            ),
            status,
          },
        });
        // Detach the visible-stream ref without closing it: ownership
        // moved to the background registry on `registerStream`.
        streamRef.current = null;
        if (reconnectTimeoutRef.current !== null) {
          window.clearTimeout(reconnectTimeoutRef.current);
          reconnectTimeoutRef.current = null;
        }
      }

      const warm = bg.thaw(nextConversationId);
      if (warm) {
        // Restore visible state from the warm slot — no fetch, no
        // replay, no flicker. Stream (if any) is already live in the
        // registry; pull its run id out of the slot so resume is a
        // no-op.
        setConversationId(nextConversationId);
        setItems(warm.items);
        setCitations(warm.citations);
        setCitationLinks(warm.citationLinks);
        setSourcesMap(warm.sources);
        setActiveRunId(warm.activeRunId);
        setLatestRunEvent(warm.latestRunEvent);
        setShowConnectorSuggestions(false);
        activeRunUserMessageIdsRef.current = new Map(warm.userMessageIdByRunId);
        latestReplaySequenceByRunRef.current = new Map(
          warm.latestSequenceByRunId,
        );
        latestSequenceRef.current = warm.activeRunId
          ? (warm.latestSequenceByRunId.get(warm.activeRunId) ?? 0)
          : 0;
        setStatus(warm.status);
        return;
      }

      try {
        setStatus("Opening conversation...");
        const [conversation, history] = await Promise.all([
          getConversation(nextConversationId, identity),
          loadHistoryItems(nextConversationId),
        ]);
        setConversationId(nextConversationId);
        setConversations((current) =>
          upsertConversation(current, conversation),
        );
        latestReplaySequenceByRunRef.current = history.latestSequenceByRunId;
        setItems(history.items);
        setCitations(history.citations);
        setCitationLinks(history.citationLinks);
        setLatestRunEvent(null);
        setActiveRunId(null);
        setShowConnectorSuggestions(false);
        setStatus(history.replayFailed ? historyReplayWarning : "Ready");
      } catch (err) {
        setStatus(errorMessage(err, "Could not open conversation"));
      }
    },
    [
      activeRunId,
      bg,
      citations,
      identity,
      items,
      latestRunEvent,
      loadHistoryItems,
      sourcesMap,
      status,
    ],
  );

  const submitUserMessage = useCallback(
    async (
      message: AppendMessage,
      options: SubmitMessageOptions = {},
    ): Promise<void> => {
      const text = textFromAppendMessage(message).trim();
      if (!text || activeRunId !== null) {
        return;
      }
      const localMessageId =
        options.optimisticMessageId ??
        appendMessageId(message) ??
        `local-${Date.now()}`;
      const runtimeUserInput = text;
      const content = contentFromAppendMessage(message);
      const attachments = attachmentsFromAppendMessage(message);
      const quote = quoteFromAppendMessage(message);
      const parentMessageId =
        options.parentMessageId === undefined
          ? lastMessageId(items)
          : options.parentMessageId;
      let targetConversationId = conversationId;
      try {
        if (targetConversationId === null) {
          setStatus("Creating chat...");
          const conversation = await createConversation(identity, {
            title: titleFromPrompt(text),
          });
          targetConversationId = conversation.conversation_id;
          setConversationId(targetConversationId);
          setConversations((current) =>
            upsertConversation(current, conversation),
          );
        }

        if (!options.optimisticMessageId) {
          setItems((current) => [
            ...current,
            optimisticUserMessage({
              id: localMessageId,
              text,
              content: content as Exclude<ChatThreadMessage["content"], string>,
              parentId: parentMessageId ?? null,
              attachments: completeAttachmentsFromAppendMessage(message),
              metadata: metadataFromAppendMessage(message),
              sourceMessageId: options.sourceMessageId ?? null,
              branchId: options.branchId ?? null,
            }),
          ]);
        }
        const run = await createRun(
          targetConversationId,
          runtimeUserInput,
          identity,
          {
            // chats-canvas-prd §16 — depth rides as a top-level
            // `reasoning_depth` field on the wire; the model selection
            // is unchanged. The `applyDepth(model, depth)` hack was a
            // workaround from before the wire field landed.
            model: modelSelectionForId(demoModels, selectedModelId),
            reasoningDepth: depth,
            attachments,
            content,
            quote,
            parentMessageId,
            sourceMessageId: options.sourceMessageId,
            branchId: options.branchId,
          },
        );
        activeRunUserMessageIdsRef.current.set(run.run_id, run.user_message_id);
        setItems((current) =>
          current.map((item) =>
            item.kind === "message" && item.id === localMessageId
              ? {
                  ...item,
                  id: run.user_message_id,
                  runId: run.run_id,
                  parentId: parentMessageId ?? null,
                }
              : item,
          ),
        );
        latestSequenceRef.current = 0;
        setActiveRunId(run.run_id);
        setLatestRunEvent(null);
        setStatus("Queued...");
        startEventStream(
          run.run_id,
          latestSequenceRef.current,
          targetConversationId,
        );
        void refreshConversations();
      } catch (err) {
        setItems((current) => [
          ...current,
          {
            id: `error-${Date.now()}`,
            kind: "status",
            title: "Message failed",
            text: errorMessage(err, "Could not send message"),
          },
        ]);
        setStatus("Ready");
      }
    },
    [
      activeRunId,
      conversationId,
      depth,
      identity,
      items,
      refreshConversations,
      selectedModelId,
      startEventStream,
    ],
  );

  const onNew = useCallback(
    async (message: AppendMessage): Promise<void> => {
      await submitUserMessage(message);
    },
    [submitUserMessage],
  );

  /**
   * Welcome-card click. The empty-thread suggestion grid (`ThreadWelcome`)
   * passes the picked prompt up here; we wrap it in a minimal
   * `AppendMessage` so the submit pipeline (optimistic message → run
   * creation → SSE stream) is identical to a typed prompt.
   */
  const onSelectSuggestion = useCallback(
    async (prompt: string): Promise<void> => {
      const message = {
        role: "user",
        content: [{ type: "text", text: prompt }],
        attachments: [],
        parentId: null,
        sourceId: null,
        runConfig: undefined,
      } as unknown as AppendMessage;
      await submitUserMessage(message);
    },
    [submitUserMessage],
  );

  const onEdit = useCallback(
    async (message: AppendMessage): Promise<void> => {
      const parentMessageId = appendMessageParentId(message);
      await submitUserMessage(message, {
        parentMessageId,
        sourceMessageId: sourceMessageIdForEdit(
          items,
          message,
          parentMessageId,
        ),
        branchId: nextBranchId(),
      });
    },
    [items, submitUserMessage],
  );

  const onCancel = useCallback(async (): Promise<void> => {
    if (activeRunId === null) {
      return;
    }
    const cancelledRunId = activeRunId;
    // Optimistic settle. Same primitive the reducer applies on
    // `run_cancelled` (`markPendingInteractionsCancelled` flips pending
    // approval / mcp_auth parts to a resolved-cancelled `result`) plus an
    // incomplete-cancelled message status. Without this the auto-resume
    // effect scans items for unresolved interaction parts and re-binds
    // activeRunId within a render — the Stop button reappears and looks
    // like the click did nothing.
    setItems((current) =>
      markPendingInteractionsCancelled(current, cancelledRunId).map((item) =>
        item.kind === "message" &&
        item.role === "assistant" &&
        item.runId === cancelledRunId
          ? { ...item, status: { type: "incomplete", reason: "cancelled" } }
          : item,
      ),
    );
    if (reconnectTimeoutRef.current !== null) {
      window.clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    streamRef.current?.close();
    streamRef.current = null;
    bg.closeStream(cancelledRunId);
    setActiveRunId(null);
    setLatestRunEvent(null);
    setStatus("Cancelling...");
    try {
      await cancelRun(cancelledRunId, identity);
    } catch (err) {
      setStatus(errorMessage(err, "Could not cancel run"));
    }
  }, [activeRunId, bg, identity]);

  const onStartNewChat = useCallback(() => {
    // PR 2.2.1 — `+ New chat` no longer tears down a running stream.
    // If the current conversation is mid-run we freeze it into the
    // background slot store (its SSE stays in the registry, keeps
    // streaming, surfaces in the sidebar live-set), then clear the
    // visible state to the welcome screen.
    const previousConvId = visibleConvIdRef.current;
    if (previousConvId !== null) {
      bg.freezeVisible({
        conversationId: previousConvId,
        snapshot: {
          items,
          citations,
          citationLinks,
          sources: sourcesMap,
          activeRunId,
          latestRunEvent,
          userMessageIdByRunId: new Map(activeRunUserMessageIdsRef.current),
          latestSequenceByRunId: new Map(latestReplaySequenceByRunRef.current),
          status,
        },
      });
      streamRef.current = null;
      if (reconnectTimeoutRef.current !== null) {
        window.clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
    }
    setActiveRunId(null);
    setConversationId(null);
    setItems([]);
    setCitations(emptyCitationRegistry());
    setCitationLinks(emptyCitationLinkRegistry());
    // PR 3.2 — clear workspace-pane data feeds so a stale conversation's
    // sources / drafts don't leak into the empty welcome state.
    setSourcesMap(emptySourceMap());
    setSourcesError(null);
    setLatestRunEvent(null);
    setShowConnectorSuggestions(false);
    activeRunUserMessageIdsRef.current = new Map();
    latestReplaySequenceByRunRef.current = new Map();
    latestSequenceRef.current = 0;
    setStatus("Ready");
  }, [activeRunId, bg, citations, items, latestRunEvent, sourcesMap, status]);

  const onShare = useCallback(async (): Promise<void> => {
    if (typeof window === "undefined" || !navigator.clipboard) {
      setStatus("Copy this page URL to share the chat.");
      return;
    }
    try {
      await navigator.clipboard.writeText(window.location.href);
      setStatus(conversationId ? "Thread link copied." : "Chat link copied.");
    } catch {
      setStatus("Could not copy share link.");
    }
  }, [conversationId]);

  async function onApprovalDecision(
    approvalId: string,
    decision: ApprovalDecision,
    answer?: string,
    // PR 1.4 — required iff `decision === "forwarded"`.
    forwardTo?: { kind: "workspace_user"; user_id: string } | null,
  ): Promise<void> {
    if (pendingApprovalDecisionsRef.current.has(approvalId)) {
      return;
    }
    pendingApprovalDecisionsRef.current.add(approvalId);
    try {
      await decideApproval(
        approvalId,
        decision,
        identity,
        undefined,
        answer,
        forwardTo ?? undefined,
      );
      // Approve / reject path is locally optimistic (we know the final
      // state). Forward leaves the run waiting on the recipient, so we
      // skip the optimistic resolve and let the trailing SSE
      // `approval_resolved` (status=forwarded) + `approval_forwarded`
      // events flip the inline card to the "Waiting on @x" pill.
      if (decision !== "forwarded") {
        setItems((current) =>
          resolveApprovalDecision(current, approvalId, decision, answer),
        );
      }
    } catch (err) {
      setItems((current) => [
        ...current,
        {
          id: `approval-error-${Date.now()}`,
          kind: "status",
          title: "Approval failed",
          text: errorMessage(err, "Could not submit approval decision"),
        },
      ]);
    } finally {
      pendingApprovalDecisionsRef.current.delete(approvalId);
    }
  }

  const onMcpAuthConnect = useCallback(
    async ({
      approvalId,
      serverId,
    }: {
      approvalId: string;
      serverId: string;
    }): Promise<void> => {
      try {
        rememberPendingMcpAuthAction({ approvalId, serverId });
        await connectors.authenticate(serverId);
      } catch (err) {
        setItems((current) => [
          ...current,
          {
            id: `mcp-auth-error-${Date.now()}`,
            kind: "status",
            title: "Connector auth failed",
            text: errorMessage(err, "Could not start connector auth"),
          },
        ]);
      }
    },
    [connectors],
  );

  const onMcpAuthSkip = useCallback(
    async ({
      serverId,
    }: {
      approvalId: string;
      serverId: string;
    }): Promise<void> => {
      try {
        await connectors.skipAuth(serverId);
        setShowConnectorSuggestions(false);
      } catch (err) {
        setItems((current) => [
          ...current,
          {
            id: `mcp-skip-error-${Date.now()}`,
            kind: "status",
            title: "Connector skip failed",
            text: errorMessage(err, "Could not skip connector auth"),
          },
        ]);
      }
    },
    [connectors],
  );

  // PR 4.4.7 Phase 2 (Slice C) — chat-mounted McpOverlay used ONLY for
  // catalog entries that need a pre-registered OAuth client (Atlassian,
  // GitHub, Intercom, PayPal, Plaid, Square). For the 1-click vendors
  // we run install + start-OAuth + redirect inline so the user clicks
  // Connect once and lands on the vendor's consent page.
  const [chatMcpOverlay, setChatMcpOverlay] = useState<{
    open: boolean;
    slug: string | null;
  }>({ open: false, slug: null });
  const onMcpInstallCatalog = useCallback(
    async ({
      slug,
      requiresPreRegisteredClient,
      approvalId,
    }: {
      slug: string;
      requiresPreRegisteredClient: boolean;
      approvalId: string;
      serverId: string;
    }): Promise<void> => {
      if (requiresPreRegisteredClient) {
        // Vendor needs the user to paste a client_id/client_secret
        // first — open the McpOverlay focused on this slug so the
        // SetupModal renders. The 1-click path below would 422 on
        // install (backend refuses without an OAuth client).
        setChatMcpOverlay({ open: true, slug });
        return;
      }
      // 1-click: install creates the ``mcp_servers`` row, authenticate
      // starts OAuth and full-page redirects to the vendor's consent
      // screen. Same chain the catalog tab's Install button runs —
      // skipping the overlay because the discovery card already
      // carried enough context to commit.
      try {
        const server = await connectors.installFromCatalog(slug);
        // Stash the discovery-card approval id keyed by the freshly
        // minted ``server_id`` *before* the authenticate call kicks
        // off the full-page redirect. The post-OAuth callback in
        // App.tsx reads this back to (a) route the user to chat
        // instead of settings and (b) flag the discovery card as
        // resolved so it transitions out of "Connecting...".
        rememberPendingMcpAuthAction({
          approvalId,
          serverId: server.server_id,
        });
        await connectors.authenticate(server.server_id);
      } catch (err) {
        // OAuth metadata discovery failures (no RFC 8414 endpoint,
        // no DCR support) classify as ``OAuthSetupRequiredError`` —
        // recover gracefully by surfacing the catalog overlay so the
        // user can paste credentials.
        if (isOAuthSetupRequired(err)) {
          setChatMcpOverlay({ open: true, slug });
          return;
        }
        setItems((current) => [
          ...current,
          {
            id: `mcp-install-error-${Date.now()}`,
            kind: "status",
            title: "Connector install failed",
            text: errorMessage(err, "Could not install connector"),
          },
        ]);
      }
    },
    [connectors],
  );
  // PR 4.4.7 Phase 2 — Skip on a catalog suggestion writes the user's
  // discoverable preference so the agent never re-suggests this
  // connector. We PATCH the preferences endpoint directly rather than
  // going through ``useDiscoverablePref`` so the chat surface doesn't
  // have to render a hook for every potential slug. Failures are
  // silent — the worst case is the agent re-suggests and the user
  // skips again.
  const onMcpMuteCatalogSuggestion = useCallback(
    ({ slug }: { slug: string }) => {
      void updateMyPreferences({
        discoverable_connectors: { overrides: { [slug]: false } },
      }).catch(() => {
        /* see comment above — best effort */
      });
    },
    [],
  );

  async function onMcpAuthDecision(
    approvalId: string,
    decision: ApprovalDecision,
  ): Promise<void> {
    if (pendingApprovalDecisionsRef.current.has(approvalId)) {
      return;
    }
    // Discovery cards (`mcp_discovery:<run_id>:<server_id>`) are UI
    // hints emitted by McpDiscoveryService — they're never persisted
    // as ApprovalRequest rows, so POSTing a decision returns 404.
    // Resolve them locally and let `connectors.authenticate` /
    // `connectors.skipAuth` (called from onMcpAuthConnect /
    // onMcpAuthSkip) drive the actual side effect.
    if (approvalId.startsWith("mcp_discovery:")) {
      if (decision === "rejected") {
        setItems((current) => resolveMcpAuthSkip(current, approvalId));
      }
      return;
    }
    pendingApprovalDecisionsRef.current.add(approvalId);
    try {
      await decideApproval(approvalId, decision, identity, "mcp_auth_resolved");
      if (decision === "rejected") {
        setItems((current) => resolveMcpAuthSkip(current, approvalId));
      }
    } catch (err) {
      setItems((current) => [
        ...current,
        {
          id: `mcp-auth-decision-error-${Date.now()}`,
          kind: "status",
          title: "Connector auth resolution failed",
          text: errorMessage(err, "Could not resolve connector auth"),
        },
      ]);
    } finally {
      pendingApprovalDecisionsRef.current.delete(approvalId);
    }
  }

  const threadMessages = useMemo<ChatThreadMessage[]>(
    () => chatItemsToThreadMessages(items, activeRunId),
    [activeRunId, items],
  );
  // PR 3.2.1 — projection over `items` for the Agents tab's expandable
  // per-subagent timeline. Reads `args.activities` already populated by
  // `upsertSubagentActivity` (live + replay). No new fetch, no new
  // store — same source of truth as the in-thread `SubagentTool`.
  const subagentActivitiesByTask = useSubagentActivities(items);
  const subagentHistoryGroups = useSubagentHistory(items);
  const handleJumpToSubagent = useCallback(
    (entry: SubagentEntry): void => {
      const selector = `[data-task-id="${CSS.escape(entry.task_id)}"]`;
      const targets = Array.from(document.querySelectorAll(selector));
      const target =
        targets.find((node) => !node.closest(".atlas-workspace-pane")) ??
        targets[0];
      target?.scrollIntoView({ block: "center", behavior: "smooth" });
      paneState.openOn("agents", { focusSubagentTaskId: entry.task_id });
    },
    [paneState],
  );
  const runUiState = useMemo(
    () =>
      deriveRunUiState({
        activeRunId,
        items,
        latestEvent: latestRunEvent,
      }),
    [activeRunId, items, latestRunEvent],
  );
  const runIndicator = useMemo(
    () =>
      activeRunId === null ||
      runUiState.phase === "waiting_for_permission" ||
      runUiState.phase === "terminal"
        ? null
        : {
            visible: runUiState.showPlanningIndicator,
            label: runUiState.planningLabel,
          },
    [activeRunId, runUiState],
  );

  // PR 2.2.1 — sidebar live-set. Union the visible chat (if it has a
  // running run) with the background-slot live-set so any number of
  // chats can pulse simultaneously. The set is rebuilt only when its
  // inputs change, so the sidebar re-renders are cheap.
  const liveConversationIds = useMemo<ReadonlySet<string>>(() => {
    if (activeRunId === null && bg.liveConvIds.size === 0) {
      return EMPTY_LIVE_CONV_SET;
    }
    const next = new Set(bg.liveConvIds);
    if (activeRunId !== null && conversationId !== null) {
      next.add(conversationId);
    }
    return next;
  }, [activeRunId, bg.liveConvIds, conversationId]);

  // Citation chips resolve against the active run's registry. When no
  // run is active (history viewing) we fall back to the most recent
  // assistant message's runId so chips on archived turns still resolve.
  const activeCitations = useMemo(() => {
    const fallbackRunId =
      activeRunId ?? mostRecentAssistantRunId(items) ?? null;
    return citationsForRun(citations, fallbackRunId);
  }, [activeRunId, citations, items]);

  // PR 1.1-rev2 / Phase 4e/4f — merge legacy sources with cited tool
  // invocations so the right-rail SourcesTab populates from the new
  // ``[[N]]`` path even when no ``source_ingested`` events fire (the
  // common case for MCP servers + DuckDuckGo, where the legacy
  // projector's shape detection misses).
  //
  // Tool invocations are derived from the existing ``items`` content
  // tree; cited entries are projected into ``SourceEntry`` shape and
  // unioned with the legacy map. Synthetic ``citation_id`` /
  // ``source_doc_id`` prefixes (``tool:`` / ``tool-call:``) keep the
  // two paths from key-colliding.
  const toolIndex = useMemo(() => toolInvocationIndex(items), [items]);
  const sourcesWithToolCitations = useMemo<SourceEntryMap>(() => {
    // Conversation-scoped: scan every run in the registry, not only the
    // most-recent assistant message's run. After an approval interrupt,
    // ``citation_made`` events fire on the resumed run while the
    // assistant message metadata may carry a sibling run id, and a
    // single-run filter would silently drop those citations.
    //
    // PR 04 — every ``citation_made`` link arrives with a non-empty
    // ``source_tool_call_id`` (the runtime allocator binds every
    // ordinal to the LangGraph tool_call_id). The projection no longer
    // needs an FE-side ordinal-position fallback.
    const cited = citedToolSources({
      runId: null,
      citationLinks,
      toolIndex,
    });
    if (cited.length === 0) {
      return sourcesMap;
    }
    const merged = new Map(sourcesMap);
    for (const entry of cited) {
      const key = `${entry.source_connector}:${entry.source_doc_id}`;
      // Legacy entries take precedence — their ``source_ingested``
      // payload carries richer metadata (URL, freshness) that the
      // tool snapshot can't reproduce.
      if (!merged.has(key)) {
        merged.set(key, entry);
      }
    }
    return merged;
  }, [citationLinks, sourcesMap, toolIndex]);

  // PR 3.5 / G9 — set of runIds whose assistant message has reached a
  // terminal status (complete/incomplete). Used by `useRunCitations` so
  // `MessageSourcesStrip` only renders once the run is sealed; the inline
  // chips already cover the live case via the active-run registry above.
  const terminalRuns = useMemo<ReadonlySet<string>>(() => {
    const set = new Set<string>();
    for (const item of items) {
      if (
        item.kind === "message" &&
        item.role === "assistant" &&
        item.runId &&
        isTerminalAssistantStatus(item.status)
      ) {
        set.add(item.runId);
      }
    }
    return set;
  }, [items]);

  // PR 3.2 — pure projection over `items` for the Approvals tab.
  const approvalsQueue = useApprovalsQueue(items);

  // PR 3.2 — auto-open the workspace pane on first non-empty data feed
  // for a conversation visit (per Atlas spec). Honours the user's
  // manual close memory: if they've closed the pane in this conv this
  // session, the signal is suppressed.
  useWorkspacePaneAutoOpenSignal({
    conversationId,
    sourceCount: sourcesMap.size,
    subagentCount: subagentsState.subagents.size,
    draftCount: draftsState.drafts.length,
    pendingApprovalsCount: approvalsQueue.pending.length,
    suppressed: paneState.isAutoOpenSuppressed(conversationId),
    onAutoOpen: paneState.openOn,
  });

  // PR 3.2 — viewport-overlay mode below 1100px so the pane never
  // pushes the chat off-screen. CSS handles the actual fixed-positioning;
  // this flag carries the state to the pane via a data-attr.
  const overlayMode = useViewportOverlay(1100);

  const attachmentAdapter = useMemo(
    () =>
      new AtlasCompositeAttachmentAdapter([
        new AtlasImageAttachmentAdapter(),
        new AtlasTextAttachmentAdapter(),
        new AtlasFileAttachmentAdapter(),
      ]),
    [],
  );
  const dictationAdapter = useMemo(
    () =>
      typeof window !== "undefined" &&
      AtlasWebSpeechDictationAdapter.isSupported()
        ? new AtlasWebSpeechDictationAdapter()
        : undefined,
    [],
  );
  // Composer imperative handle. The skill picker (workspace pane)
  // calls `setText` here; the Composer also exposes `submit` /
  // `addAttachment` for programmatic flows.
  const composerHandleRef = useRef<ComposerHandle | null>(null);
  // Tracks which user message the user is currently inline-editing.
  // ThreadBody renders `UserEditComposer` for that message; cancel +
  // save callbacks live here.
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);

  // PR 2.1 — current conversation row + per-chat connector glyphs feed
  // for the topbar pills. Read-only here; ConnectorPopover (PR 3.4)
  // owns the write paths into useConversationConnectors.patch.
  const currentConversation = useMemo(
    () =>
      conversationId === null
        ? null
        : (conversations.find(
            (entry) => entry.conversation_id === conversationId,
          ) ?? null),
    [conversationId, conversations],
  );
  const conversationScopes = useConversationConnectors(
    currentConversation,
    identity,
  );
  // PR 3.4 — projection of the workspace-installed catalog + per-chat
  // overrides into the four-state row vocabulary the popover renders.
  // Memoised so the popover doesn't re-mount its keyboard-nav handler
  // on every parent render.
  // PR 4.4.6 — the per-chat popover only shows **Connected** connectors
  // (installed AND authorized). Catalog availability lives in Settings →
  // Manage MCP servers; the chat surface never renders disconnected /
  // workspace-off rows. ``projectChatConnectors`` filters then projects.
  const connectorRows = useMemo(
    () => projectChatConnectors(connectors.servers, conversationScopes.scopes),
    [connectors.servers, conversationScopes.scopes],
  );
  const connectorsActiveCount = useMemo(
    () => activeConnectorCount(connectorRows),
    [connectorRows],
  );

  // PR 3.4 — single open-state owner for the per-chat ConnectorPopover.
  // The popover is anchored to the composer button (the topbar pill was
  // removed — connectors live in the composer only). Boolean is
  // sufficient now that there's a single anchor.
  const [connectorsOpen, setConnectorsOpen] = useState(false);
  const composerConnectorsRef = useRef<HTMLButtonElement | null>(null);
  const closeConnectorsPopover = useCallback(
    () => setConnectorsOpen(false),
    [],
  );
  const onComposerConnectorsOpen = useCallback(
    () => setConnectorsOpen((prev) => !prev),
    [],
  );
  // Close the popover when the conversation switches — the rows are
  // about to change underneath the user, and the per-chat scopes would
  // momentarily disagree with the open popover's state.
  useEffect(() => {
    setConnectorsOpen(false);
  }, [conversationId]);

  const renderConnectorPopover = useCallback(
    (
      triggerRef: React.RefObject<HTMLElement | null>,
      placement: "down" | "up",
    ) => (
      <ConnectorPopover
        open
        onClose={closeConnectorsPopover}
        triggerRef={triggerRef}
        rows={connectorRows}
        onToggle={(serverId, nextScopes) => {
          void conversationScopes
            .patch({ [serverId]: nextScopes ?? null })
            .catch(() => {
              // Hook surfaces the error string; it renders inline.
            });
        }}
        onConnect={(serverId) => {
          void connectors.authenticate(serverId);
        }}
        onEnableInSettings={() => {
          closeConnectorsPopover();
          onOpenSettings("connectors");
        }}
        onManage={() => onOpenSettings("connectors")}
        placement={placement}
        error={conversationScopes.error}
        readOnly={currentConversation === null}
        runInProgress={activeRunId !== null}
      />
    ),
    [
      closeConnectorsPopover,
      connectorRows,
      conversationScopes,
      connectors,
      onOpenSettings,
      currentConversation,
      activeRunId,
    ],
  );
  const selectedModel = useMemo(
    () => demoModels.find((model) => model.id === selectedModelId) ?? null,
    [selectedModelId],
  );
  const depthVisible = modelSupportsDepth(selectedModel);

  const handleReload = useCallback(
    async (parentId?: string | null): Promise<void> => {
      if (activeRunId !== null || conversationId === null) {
        return;
      }
      const parentMessageId = parentId ?? lastUserMessageId(items);
      if (!parentMessageId) {
        return;
      }
      const sourceMessageId = latestAssistantChildId(items, parentMessageId);
      const run = await createRun(
        conversationId,
        userTextForMessage(items, parentMessageId) ??
          REGENERATE_PREVIOUS_RESPONSE_PROMPT,
        identity,
        {
          // chats-canvas-prd §16 — depth as top-level `reasoning_depth`.
          model: modelSelectionForId(demoModels, selectedModelId),
          reasoningDepth: depth,
          parentMessageId,
          sourceMessageId,
          regenerateFromMessageId: sourceMessageId ?? parentMessageId,
          branchId: nextBranchId(),
        },
      );
      activeRunUserMessageIdsRef.current.set(run.run_id, run.user_message_id);
      latestSequenceRef.current = 0;
      setActiveRunId(run.run_id);
      setLatestRunEvent(null);
      setStatus("Queued...");
      startEventStream(run.run_id, latestSequenceRef.current, conversationId);
    },
    [
      activeRunId,
      conversationId,
      depth,
      identity,
      items,
      selectedModelId,
      startEventStream,
    ],
  );

  /**
   * Tool-call interrupt resolution. The footer-button → `<MessageParts>` →
   * tool renderer chain calls this with the tool's `resume` payload; we
   * dispatch by approval_kind to the matching decision handler. The same
   * shape is also forwarded into `useExternalStoreRuntime` so the
   * assistant-ui primitive paths still functioning during migration
   * keep working.
   */
  const handleResumeToolCall = useCallback(
    (payload: unknown): void => {
      if (isMcpAuthResumePayload(payload)) {
        void onMcpAuthDecision(payload.approval_id, payload.decision);
        return;
      }
      if (isApprovalResumePayload(payload)) {
        void onApprovalDecision(
          payload.approval_id,
          payload.decision,
          payload.answer,
          payload.forward_to ?? undefined,
        );
      }
    },
    // onApprovalDecision / onMcpAuthDecision are stable function refs in
    // this component (declared with useCallback or as named functions),
    // so we omit them from the dep list to avoid resurrecting them on
    // every render and re-creating the runtime.
    [],
  );

  // PR 4.4.6.4 — side-channel POST to ``/v1/agent/approvals/{id}/undo``.
  // Side-channel because it's *not* a LangGraph resume: the run already
  // completed; this records the user's intent + audits + emits a stream
  // event. UndoableReceipt threads the result back into local state.
  const handleRequestUndo = useCallback(
    async (approvalId: string): Promise<{ undo_requested_at: string }> => {
      if (identity === null) {
        throw new Error("Not signed in.");
      }
      const response = await requestApprovalUndo(approvalId, identity);
      return { undo_requested_at: response.undo_requested_at };
    },
    [identity],
  );

  /**
   * Footer Reload click handler. ThreadBody passes the assistant
   * message id; we resolve the parent user message via `parentId` on
   * the assistant's `ChatItem` row.
   */
  const handleReloadFromAssistant = useCallback(
    (assistantMessageId: string): void => {
      const item = items.find(
        (candidate): candidate is Extract<ChatItem, { kind: "message" }> =>
          candidate.kind === "message" && candidate.id === assistantMessageId,
      );
      const parentId = item?.parentId ?? undefined;
      void handleReload(parentId ?? undefined);
    },
    [handleReload, items],
  );

  // Composer submission. Wraps the Composer's `{text, attachments}`
  // payload in the `AppendMessage` shape that `submitUserMessage`
  // expects, then runs the same optimistic-message → run-creation →
  // SSE-stream pipeline as a typed prompt.
  const onComposerSubmit = useCallback(
    async ({
      text,
      attachments,
    }: {
      text: string;
      attachments: ReadonlyArray<CompleteAttachment>;
    }): Promise<void> => {
      const append = {
        role: "user",
        content: [{ type: "text", text }],
        attachments,
        parentId: null,
        sourceId: null,
        runConfig: undefined,
      } as unknown as AppendMessage;
      await submitUserMessage(append);
    },
    [submitUserMessage],
  );

  const onAttachComposerSkill = useCallback((skill: Skill): void => {
    setSelectedComposerSkills((current) => {
      if (current.some((item) => item.skill_id === skill.skill_id)) {
        return current;
      }
      return [...current, skill];
    });
    requestAnimationFrame(() => composerHandleRef.current?.focus());
  }, []);

  const onRemoveComposerSkill = useCallback((skillId: string): void => {
    setSelectedComposerSkills((current) =>
      current.filter((skill) => skill.skill_id !== skillId),
    );
    requestAnimationFrame(() => composerHandleRef.current?.focus());
  }, []);

  const onClearComposerSkills = useCallback((): void => {
    setSelectedComposerSkills([]);
  }, []);

  // Inline-edit save. Resolves the parent of the source message and
  // dispatches a fresh run with `branchId` so the original assistant
  // child stays in the tree.
  const onEditSave = useCallback(
    async (sourceMessageId: string, text: string): Promise<void> => {
      const item = items.find(
        (candidate): candidate is Extract<ChatItem, { kind: "message" }> =>
          candidate.kind === "message" && candidate.id === sourceMessageId,
      );
      const parentId = item?.parentId ?? null;
      const append = {
        role: "user",
        content: [{ type: "text", text }],
        attachments: [],
        parentId,
        sourceId: sourceMessageId,
        runConfig: undefined,
      } as unknown as AppendMessage;
      await submitUserMessage(append, {
        parentMessageId: parentId,
        sourceMessageId,
        branchId: nextBranchId(),
      });
      setEditingMessageId(null);
    },
    [items, submitUserMessage],
  );

  return (
    <>
      <ApprovalFocusProvider>
        <main
          className={[
            "aui-workspace",
            sidebarCollapsed && "aui-workspace--sidebar-collapsed",
            paneState.open && !overlayMode && "aui-workspace--pane-open",
          ]
            .filter(Boolean)
            .join(" ")}
          data-pane-overlay={overlayMode ? "true" : "false"}
        >
          <AssistantThreadList
            activeConversationId={conversationId}
            liveConversationIds={liveConversationIds}
            collapsed={sidebarCollapsed}
            conversations={conversations}
            loading={historyLoading}
            onOpenSettings={() => onOpenSettings("profile")}
            onRefresh={() => void refreshConversations()}
            onSwitchToThread={(id) => void loadConversationById(id)}
            onStartNewChat={onStartNewChat}
            onToggleSidebar={() => setSidebarCollapsed((current) => !current)}
            onSwitchWorkspace={(orgId) => {
              // PR 3.5 / G4 — cancel-then-switch when a run is streaming
              // (PR 2.2 §3.7); auth.switchWorkspace then hard-navs.
              if (activeRunId !== null) {
                void onCancel().then(() => auth.switchWorkspace(orgId));
                return;
              }
              void auth.switchWorkspace(orgId);
            }}
            onTogglePin={pinned.togglePinned}
            onArchive={(id) => void onArchiveConversation(id)}
            pinnedIds={pinned.pinnedIds}
          />
          <SourcePreviewProvider>
            <AssistantThread
              topbar={
                <Topbar
                  workspace={null}
                  folder={currentConversation?.folder ?? null}
                  title={currentConversation?.title ?? null}
                  onRenameTitle={
                    currentConversation === null
                      ? undefined
                      : async (next: string) => {
                          const renamed = await updateConversation(
                            currentConversation.conversation_id,
                            { title: next.trim() === "" ? null : next },
                            identity,
                          );
                          setConversations((current) =>
                            current.map((entry) =>
                              entry.conversation_id === renamed.conversation_id
                                ? renamed
                                : entry,
                            ),
                          );
                        }
                  }
                  runUiState={
                    historyError !== null
                      ? { ...runUiState, headerStatus: historyError }
                      : oauthStatus !== null
                        ? { ...runUiState, headerStatus: oauthStatus }
                        : activeRunId === null
                          ? { ...runUiState, headerStatus: status }
                          : runUiState
                  }
                  sidebarCollapsed={sidebarCollapsed}
                  onToggleSidebar={() =>
                    setSidebarCollapsed((current) => !current)
                  }
                  panelOpen={paneState.open}
                  onTogglePanel={() => paneState.toggle()}
                  usagePct={null}
                  onOpenUsage={() => setDetailsPanel("usage")}
                  models={demoModels}
                  selectedModel={selectedModelId}
                  onModelChange={setSelectedModelId}
                  depth={depth}
                  onDepthChange={setDepth}
                  depthVisible={depthVisible}
                  onShare={() => void onShare()}
                  shareSlot={
                    <SharePopover
                      chatTitle={currentTitle(conversations, conversationId)}
                      chatUrl={
                        typeof window !== "undefined"
                          ? window.location.href
                          : ""
                      }
                      conversationId={conversationId}
                      identity={identity}
                      onStatus={(message) => setStatus(message)}
                    />
                  }
                  onOpenSettings={() => onOpenSettings("profile")}
                />
              }
            >
              <CitationsProvider
                citations={activeCitations}
                byRun={citations}
                terminalRuns={terminalRuns}
                linksByRun={citationLinks}
                activeRunId={activeRunId}
                onOrdinalSelect={(citationId) =>
                  paneState.openOn("sources", {
                    focusCitationId: citationId,
                  })
                }
              >
                <SubagentFleetProvider
                  value={{
                    subagentsByTask: subagentsState.subagents,
                    activitiesByTask: subagentActivitiesByTask,
                    onJumpToApproval: scrollChatToEvent,
                    onOpenWorkspace: () => paneState.openOn("agents"),
                  }}
                >
                  <ThreadBody
                    ref={composerHandleRef}
                    messages={threadMessages}
                    running={activeRunId !== null}
                    disabled={false}
                    attachmentAdapter={attachmentAdapter}
                    editingMessageId={editingMessageId}
                    onEditCancel={() => setEditingMessageId(null)}
                    onEditSave={(sourceMessageId, text) =>
                      void onEditSave(sourceMessageId, text)
                    }
                    onSubmit={onComposerSubmit}
                    onCancel={() => void onCancel()}
                    connectors={connectors}
                    skills={skills}
                    onMcpAuthConnect={onMcpAuthConnect}
                    onMcpAuthSkip={onMcpAuthSkip}
                    onMcpInstallCatalog={onMcpInstallCatalog}
                    onMcpMuteCatalogSuggestion={onMcpMuteCatalogSuggestion}
                    onOpenMcpSettings={() => onOpenSettings("connectors")}
                    onOpenSkillsSettings={() => onOpenSettings("skills")}
                    onShowConnectors={() => setShowConnectorSuggestions(true)}
                    onOpenDetailsPanel={(kind) => setDetailsPanel(kind)}
                    onOpenSkillsPanel={() => paneState.openOn("skills")}
                    selectedSkills={selectedComposerSkills}
                    onAttachSkill={onAttachComposerSkill}
                    onRemoveSkill={onRemoveComposerSkill}
                    onClearSkills={onClearComposerSkills}
                    onOpenSources={(citationId) =>
                      paneState.openOn("sources", {
                        focusCitationId: citationId,
                      })
                    }
                    runIndicator={runIndicator}
                    connectorsTrigger={
                      <span className="atlas-connectors-anchor atlas-connectors-anchor--composer">
                        <ComposerConnectorsButton
                          ref={composerConnectorsRef}
                          activeCount={connectorsActiveCount}
                          open={connectorsOpen}
                          onClick={onComposerConnectorsOpen}
                        />
                        {connectorsOpen
                          ? renderConnectorPopover(composerConnectorsRef, "up")
                          : null}
                      </span>
                    }
                    activeModelLabel={selectedModel?.name}
                    models={demoModels}
                    selectedModel={selectedModelId}
                    onModelChange={setSelectedModelId}
                    depth={depth}
                    onDepthChange={setDepth}
                    depthVisible={depthVisible}
                    connectorSuggestions={
                      showConnectorSuggestions &&
                      suggestedServers.length > 0 ? (
                        <ConnectorSuggestionCard
                          servers={suggestedServers}
                          onConnect={(serverId) =>
                            void connectors.authenticate(serverId)
                          }
                          onSkip={(serverId) =>
                            void connectors.skipAuth(serverId)
                          }
                          onNone={() => setShowConnectorSuggestions(false)}
                        />
                      ) : null
                    }
                    onSelectSuggestion={(prompt) =>
                      void onSelectSuggestion(prompt)
                    }
                    onResumeToolCall={handleResumeToolCall}
                    onReload={handleReloadFromAssistant}
                    onRequestUndo={handleRequestUndo}
                  />
                </SubagentFleetProvider>
              </CitationsProvider>
            </AssistantThread>
            <WorkspacePane
              state={paneState}
              sources={sourcesWithToolCitations}
              sourcesLoading={sourcesLoading}
              sourcesError={sourcesError}
              sourcesSearching={runUiState.phase === "acting"}
              onSelectSource={(source) =>
                scrollChatToCitation(source.citation_id)
              }
              onJumpToChatSource={(source) =>
                scrollChatToCitation(source.citation_id)
              }
              subagents={subagentsState.subagents}
              subagentsLoading={subagentsState.loading}
              subagentsError={subagentsState.error}
              subagentActivitiesByTask={subagentActivitiesByTask}
              subagentHistoryGroups={subagentHistoryGroups}
              onJumpToSubagent={handleJumpToSubagent}
              draft={draftsState.latest}
              draftLoading={draftsState.loading}
              draftError={draftsState.error}
              onPatchDraft={(request) =>
                draftsState.latest === null
                  ? Promise.reject(new Error("No active draft"))
                  : draftsState.patch(draftsState.latest.draft_id, request)
              }
              onSendDraft={(request) =>
                draftsState.latest === null
                  ? Promise.reject(new Error("No active draft"))
                  : draftsState.send(draftsState.latest.draft_id, request)
              }
              onDiscardDraft={(request) =>
                draftsState.latest === null
                  ? Promise.reject(new Error("No active draft"))
                  : draftsState.discard(draftsState.latest.draft_id, request)
              }
              approvalsQueue={approvalsQueue}
              skills={skills.skills}
              skillsLoading={skills.loading}
              skillsError={skills.error}
              onPickSkill={(skill) => {
                onAttachComposerSkill(skill);
                if (overlayMode) {
                  paneState.close("viewport");
                }
              }}
              onOpenSkillSettings={() => onOpenSettings("skills")}
              overlay={overlayMode}
            />
            {detailsPanel !== null ? (
              <DetailsPanelHost
                kind={detailsPanel}
                conversationId={conversationId}
                identity={identity}
                sources={sourcesWithToolCitations}
                onClose={() => setDetailsPanel(null)}
              />
            ) : null}
          </SourcePreviewProvider>
        </main>
      </ApprovalFocusProvider>
      <McpOverlay
        open={chatMcpOverlay.open}
        installSlug={chatMcpOverlay.slug}
        onClose={() => setChatMcpOverlay({ open: false, slug: null })}
        connectors={connectors}
      />
    </>
  );
}

async function replayEventsForMessages(
  messages: Message[],
  identity: RequestIdentity,
): Promise<{
  eventsByRunId: Map<string, RuntimeEventEnvelope[]>;
  replayFailed: boolean;
}> {
  const runIds = Array.from(
    new Set(
      messages.flatMap((message) => (message.run_id ? [message.run_id] : [])),
    ),
  );
  const eventsByRunId = new Map<string, RuntimeEventEnvelope[]>();
  let replayFailed = false;
  await Promise.all(
    runIds.map(async (runId) => {
      try {
        const replay = await replayRunEvents(runId, identity);
        eventsByRunId.set(runId, replay.events);
      } catch {
        replayFailed = true;
      }
    }),
  );
  return { eventsByRunId, replayFailed };
}

function textFromAppendMessage(message: AppendMessage): string {
  const textParts: string[] = [];
  for (const part of message.content) {
    if (part.type === "text") {
      textParts.push(part.text);
    }
  }
  return textParts.join("\n");
}

function contentFromAppendMessage(
  message: AppendMessage,
): NonNullable<CreateRunRequest["content"]> {
  return message.content.map(normalizeRunContentPart);
}

function completeAttachmentsFromAppendMessage(
  message: AppendMessage,
): ChatThreadMessage["attachments"] {
  return message.attachments && message.attachments.length > 0
    ? (message.attachments as ChatThreadMessage["attachments"])
    : undefined;
}

function attachmentsFromAppendMessage(
  message: AppendMessage,
): NonNullable<CreateRunRequest["attachments"]> {
  const attachments = message.attachments ?? [];
  return attachments.map((attachment: CompleteAttachment) => ({
    id: attachment.id,
    type: attachment.type,
    name: attachment.name,
    content_type: attachment.contentType ?? null,
    size: attachment.file?.size ?? null,
    content: attachment.content.map(normalizeRunContentPart),
  }));
}

function normalizeRunContentPart(
  part:
    | AppendMessage["content"][number]
    | CompleteAttachment["content"][number],
): NonNullable<CreateRunRequest["content"]>[number] {
  if (part.type === "file") {
    // OpaqueMessagePart can satisfy `type: "file"` with loose field
    // typing; assert the concrete shape at this boundary so the
    // wire payload is correctly typed.
    const file = part as {
      type: "file";
      filename: string;
      data: string;
      mimeType: string;
    };
    return {
      type: "file",
      filename: file.filename,
      data: file.data,
      mime_type: file.mimeType,
    };
  }
  return { ...part } as NonNullable<CreateRunRequest["content"]>[number];
}

function quoteFromAppendMessage(
  message: AppendMessage,
): Record<string, unknown> | undefined {
  const quote = message.metadata?.custom?.quote;
  return quote && typeof quote === "object"
    ? (quote as Record<string, unknown>)
    : undefined;
}

function metadataFromAppendMessage(
  message: AppendMessage,
): ThreadMessageLike["metadata"] {
  const custom = message.metadata?.custom;
  return custom && Object.keys(custom).length > 0 ? { custom } : undefined;
}

function appendMessageParentId(message: AppendMessage): string | null {
  const parentId = (message as { parentId?: unknown }).parentId;
  return typeof parentId === "string" && parentId.trim() ? parentId : null;
}

function appendMessageId(message: AppendMessage): string | null {
  const id = (message as { id?: unknown }).id;
  return typeof id === "string" && id.trim() ? id : null;
}

function lastMessageId(items: ChatItem[]): string | null {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item.kind === "message") {
      return item.id;
    }
  }
  return null;
}

function lastUserMessageId(items: ChatItem[]): string | null {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item.kind === "message" && item.role === "user") {
      return item.id;
    }
  }
  return null;
}

function sourceMessageIdForEdit(
  items: ChatItem[],
  message: AppendMessage,
  parentMessageId: string | null,
): string | null {
  const messageId = appendMessageId(message);
  if (
    messageId &&
    items.some((item) => item.kind === "message" && item.id === messageId)
  ) {
    return messageId;
  }
  const siblings = items.filter(
    (item) =>
      item.kind === "message" &&
      item.role === "user" &&
      (item.parentId ?? null) === parentMessageId,
  );
  return siblings.at(-1)?.id ?? null;
}

function latestAssistantChildId(
  items: ChatItem[],
  parentMessageId: string,
): string | null {
  const children = items.filter(
    (item) =>
      item.kind === "message" &&
      item.role === "assistant" &&
      item.parentId === parentMessageId,
  );
  return children.at(-1)?.id ?? null;
}

function pendingActionRunId(items: ChatItem[]): string | null {
  for (const item of items) {
    if (item.kind !== "message" || item.role !== "assistant" || !item.runId) {
      continue;
    }
    // Skip runs that already terminated. Otherwise the auto-resume effect
    // re-binds activeRunId to a cancelled / failed / completed run whose
    // approval card is still in `items` with `result === undefined`.
    if (
      item.status?.type === "incomplete" ||
      item.status?.type === "complete"
    ) {
      continue;
    }
    if (hasPendingAction(item.content)) {
      return item.runId;
    }
  }
  return null;
}

function mostRecentAssistantRunId(items: ChatItem[]): string | null {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item.kind === "message" && item.role === "assistant" && item.runId) {
      return item.runId;
    }
  }
  return null;
}

function userMessageIdForRun(items: ChatItem[], runId: string): string | null {
  const item = items.find(
    (candidate) =>
      candidate.kind === "message" &&
      candidate.role === "user" &&
      candidate.runId === runId,
  );
  return item?.kind === "message" ? item.id : null;
}

function latestSequenceByRunId(
  eventsByRunId: ReadonlyMap<string, readonly RuntimeEventEnvelope[]>,
): Map<string, number> {
  const latestByRun = new Map<string, number>();
  for (const [runId, events] of eventsByRunId) {
    latestByRun.set(
      runId,
      events.reduce((latest, event) => Math.max(latest, event.sequence_no), 0),
    );
  }
  return latestByRun;
}

function userTextForMessage(
  items: ChatItem[],
  messageId: string,
): string | null {
  const item = items.find(
    (candidate) =>
      candidate.kind === "message" &&
      candidate.role === "user" &&
      candidate.id === messageId,
  );
  if (!item || item.kind !== "message") {
    return null;
  }
  return item.content
    .flatMap((part) => (part.type === "text" ? [part.text] : []))
    .join("\n")
    .trim();
}

/**
 * PR 2.2.1 — stable empty-set singleton so the no-live-runs path doesn't
 * thrash referential equality on every render of `<Sidebar>`.
 */
const EMPTY_LIVE_CONV_SET: ReadonlySet<string> = new Set();

function withAssistantParent(
  items: ChatItem[],
  runId: string,
  parentMessageId: string | null,
): ChatItem[] {
  if (parentMessageId === null) {
    return items;
  }
  return items.map((item) =>
    item.kind === "message" &&
    item.role === "assistant" &&
    item.runId === runId &&
    !item.parentId
      ? { ...item, parentId: parentMessageId }
      : item,
  );
}

function nextBranchId(): string {
  return `branch-${Date.now()}`;
}

function modelSelectionForId(
  models: ModelCatalogModel[],
  modelId: string,
): {
  provider?: string | null;
  model_name?: string | null;
  reasoning?: Record<string, unknown> | null;
} {
  const model = models.find((candidate) => candidate.id === modelId);
  if (!model) {
    return { model_name: modelId };
  }
  return {
    provider: model.provider,
    model_name: model.model_name,
    reasoning: model.reasoning ?? null,
  };
}

function isApprovalResumePayload(payload: unknown): payload is {
  decision: ApprovalDecision;
  approval_id: string;
  answer?: string;
  // PR 1.4 — present iff `decision === "forwarded"`. Server-side
  // validators reject malformed combinations.
  forward_to?: { kind: "workspace_user"; user_id: string } | null;
} {
  if (!payload || typeof payload !== "object") {
    return false;
  }
  const record = payload as Record<string, unknown>;
  if (
    typeof record.approval_id !== "string" ||
    record.approval_kind === "mcp_auth"
  ) {
    return false;
  }
  if (record.decision === "approved" || record.decision === "rejected") {
    return record.answer === undefined || typeof record.answer === "string";
  }
  if (record.decision === "forwarded") {
    const forward = record.forward_to;
    return (
      forward !== null &&
      typeof forward === "object" &&
      (forward as Record<string, unknown>).kind === "workspace_user" &&
      typeof (forward as Record<string, unknown>).user_id === "string"
    );
  }
  return false;
}

function isMcpAuthResumePayload(
  payload: unknown,
): payload is { decision: ApprovalDecision; approval_id: string } {
  if (!payload || typeof payload !== "object") {
    return false;
  }
  const record = payload as Record<string, unknown>;
  return (
    typeof record.approval_id === "string" &&
    record.approval_kind === "mcp_auth" &&
    (record.decision === "approved" || record.decision === "rejected")
  );
}

function titleFromPrompt(prompt: string): string {
  const normalized = prompt.replace(/\s+/g, " ").trim();
  if (normalized.length <= 44) {
    return normalized || "New chat";
  }
  return `${normalized.slice(0, 43)}...`;
}

function currentTitle(
  conversations: Conversation[],
  conversationId: string | null,
): string {
  if (conversationId === null) {
    return "New chat";
  }
  return (
    conversations.find(
      (conversation) => conversation.conversation_id === conversationId,
    )?.title ?? "Enterprise Search"
  );
}

function upsertConversation(
  conversations: Conversation[],
  conversation: Conversation,
): Conversation[] {
  const withoutExisting = conversations.filter(
    (item) => item.conversation_id !== conversation.conversation_id,
  );
  return [conversation, ...withoutExisting].sort(
    (left, right) =>
      new Date(right.updated_at).getTime() -
      new Date(left.updated_at).getTime(),
  );
}

function isTerminalRunEvent(event: RuntimeEventEnvelope): boolean {
  return (
    event.event_type === "run_completed" ||
    event.event_type === "run_cancelled" ||
    event.event_type === "run_failed"
  );
}

function statusForTerminalRunEvent(event: RuntimeEventEnvelope): string {
  if (event.event_type === "run_completed") {
    return "Ready";
  }
  if (event.event_type === "run_failed") {
    return "Could not complete";
  }
  return "Stopped";
}

const demoModels: Array<ModelCatalogModel & { disabled?: boolean }> = [
  {
    id: "gpt-5.4-nano",
    provider: "openai",
    model_name: "gpt-5.4-nano",
    name: "GPT-5.4 Nano",
    description: "Fastest OpenAI model",
    configured: true,
    supports_streaming: true,
    supports_reasoning: true,
    reasoning: { enabled: true, effort: "medium", summary: "auto" },
  },
  {
    id: "gpt-5.4-mini",
    provider: "openai",
    model_name: "gpt-5.4-mini",
    name: "GPT-5.4 Mini",
    description: "Compact OpenAI model",
    configured: true,
    supports_streaming: true,
    supports_reasoning: true,
    reasoning: { enabled: true, effort: "medium", summary: "auto" },
  },
  {
    id: "anthropic/claude-haiku-4-5",
    provider: "anthropic",
    model_name: "claude-haiku-4-5",
    name: "Claude Haiku 4.5",
    description: "Anthropic fast model",
    configured: false,
    disabled: true,
  },
  {
    id: "google-ai-studio/gemini-3-flash",
    provider: "gemini",
    model_name: "gemini-3-flash",
    name: "Gemini 3 Flash",
    description: "Google long-context model",
    configured: false,
    disabled: true,
  },
  {
    id: "grok/grok-4-1-fast",
    provider: "grok",
    model_name: "grok-4-1-fast",
    name: "Grok 4.1 Fast",
    description: "xAI fast model",
    configured: false,
    disabled: true,
  },
  {
    id: "grok/grok-3-mini-fast",
    provider: "grok",
    model_name: "grok-3-mini-fast",
    name: "Grok 3 Mini Fast",
    description: "xAI compact model",
    configured: false,
    disabled: true,
  },
  {
    id: "groq/llama-3.3-70b-versatile",
    provider: "groq",
    model_name: "llama-3.3-70b-versatile",
    name: "Llama 3.3 70B",
    description: "Groq-hosted Meta model",
    configured: false,
    disabled: true,
  },
  {
    id: "groq/qwen/qwen3-32b",
    provider: "groq",
    model_name: "qwen/qwen3-32b",
    name: "Qwen3 32B",
    description: "Groq-hosted Qwen model",
    configured: false,
    disabled: true,
  },
];

const historyReplayWarning =
  "History loaded; some activity could not be restored.";
