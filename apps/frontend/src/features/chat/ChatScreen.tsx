import type {
  ApprovalDecision,
  Conversation,
  CreateRunRequest,
  Message,
  ModelCatalogModel,
  RuntimeEventEnvelope,
} from "@enterprise-search/api-types";
import {
  AssistantRuntimeProvider,
  type Attachment,
  type AttachmentAdapter,
  CompositeAttachmentAdapter,
  type CompleteAttachment,
  type PendingAttachment,
  SimpleImageAttachmentAdapter,
  SimpleTextAttachmentAdapter,
  Suggestions,
  WebSpeechDictationAdapter,
  useAui,
  useExternalStoreRuntime,
  type AppendMessage,
  type ExternalStoreThreadData,
  type ExternalStoreThreadListAdapter,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import type { ReactElement } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  cancelRun,
  createConversation,
  createRun,
  decideApproval,
  getConversation,
  listConversations,
  listMessages,
  replayRunEvents,
  streamRunEvents,
  updateConversation,
  type AgentEventStream,
} from "../../api/agentApi";
import type { RequestIdentity } from "../../api/config";
import { ConnectorSuggestionCard } from "../connectors/ConnectorConsentCard";
import { ConnectorPopover } from "../connectors/ConnectorPopover";
import type { ConnectorState } from "../connectors/useConnectors";
import { useConversationConnectors } from "../connectors/useConversationConnectors";
import {
  activeCount as activeConnectorCount,
  projectConnectors,
} from "../connectors/projectConnectors";
import type { SkillState } from "../skills/useSkills";
import { ComposerConnectorsButton } from "./components/composer/ComposerConnectorsButton";
import {
  DetailsPanelHost,
  type DetailsPanelKind,
} from "./components/details/DetailsPanelHost";
import { Topbar, activeConnectorsFromScopes } from "./components/shell";
import { SharePopover } from "../share/SharePopover";
import {
  DEFAULT_THINKING_DEPTH,
  applyDepth,
  isThinkingDepth,
  modelSupportsDepth,
  type ThinkingDepth,
} from "./depth";
import { useLocalStorageState } from "../../utils/useLocalStorageState";
import { useViewportOverlay } from "../../utils/useViewportOverlay";
import { ApprovalFocusProvider } from "./approval/ApprovalFocusContext";

type ChatSettingsTarget = "general" | "connectors" | "skills";
import {
  applyRuntimeEvent,
  chatItemsToThreadMessages,
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
  citationsForRun,
  emptyCitationRegistry,
  type CitationRegistryByRun,
} from "./chatModel/citationsRegistry";
import { applySubagentEvent } from "./chatModel/subagentReducer";
import { applyDraftUpdatedEvent } from "./chatModel/draftsRegistry";
import { CitationsProvider } from "./components/citations/citationsContext";
import { listSources } from "../../api/agentApi";
import {
  applySourceEvent,
  emptySourceMap,
  seedSourceMap,
  type SourceEntryMap,
} from "./chatModel/sourcesReducer";
import { WorkspacePane } from "./components/workspace/WorkspacePane";
import { useWorkspacePaneState } from "./components/workspace/useWorkspacePaneState";
import { useWorkspacePaneAutoOpenSignal } from "./components/workspace/useWorkspacePaneAutoOpen";
import { useSubagents } from "./components/workspace/useSubagents";
import { useDrafts } from "./components/workspace/useDrafts";
import { useApprovalsQueue } from "./components/workspace/useApprovalsQueue";
import {
  AssistantThread,
  AssistantThreadList,
  ThreadBody,
} from "./assistantUiComponents";
import {
  CHAT_PROMPT_SUGGESTIONS,
  REGENERATE_PREVIOUS_RESPONSE_PROMPT,
} from "./prompts";
import {
  rememberPendingMcpAuthAction,
  type CompletedMcpAuthAction,
} from "./mcpAuthAction";
import { deriveRunUiState, isRunUiEvent } from "./chatRunState";
import { useAuth } from "../auth/AuthContext";
import { isTerminalAssistantStatus } from "./utils/activityDataBuilders";

type SubmitMessageOptions = {
  parentMessageId?: string | null;
  sourceMessageId?: string | null;
  branchId?: string | null;
  optimisticMessageId?: string;
};

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
  // PR 2.1 — thinking depth maps onto reasoning.effort via applyDepth.
  // Persisted across reloads; ignored when the active model doesn't
  // support reasoning. Mid-run depth changes never affect the active
  // run — the worker reads the frozen ModelConfig from runtime_context.
  const [depth, setDepth] = useLocalStorageState<ThinkingDepth>(
    "atlas:thinking-depth",
    DEFAULT_THINKING_DEPTH,
    isThinkingDepth,
  );
  const streamRef = useRef<AgentEventStream | null>(null);
  const latestSequenceRef = useRef(0);
  const reconnectTimeoutRef = useRef<number | null>(null);
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
  // PR 3.2 — Sources tab snapshot. Seeded from
  // `GET /v1/agent/conversations/{id}/sources` on conversation switch,
  // overlaid live by `applySourceEvent`. Conversation-scoped, not run-
  // scoped, because the Sources tab spans every run in the chat.
  const [sourcesMap, setSourcesMap] = useState<SourceEntryMap>(emptySourceMap);
  const [sourcesLoading, setSourcesLoading] = useState(false);
  const [sourcesError, setSourcesError] = useState<string | null>(null);

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

  const loadHistoryItems = useCallback(
    async (
      nextConversationId: string,
    ): Promise<{
      items: ChatItem[];
      replayFailed: boolean;
      latestSequenceByRunId: Map<string, number>;
      citations: CitationRegistryByRun;
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
    };
  }, [identity, loadHistoryItems]);

  useEffect(() => {
    setItems((current) =>
      resolveAuthenticatedMcpServers(current, connectors.servers),
    );
  }, [connectors.servers]);

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
        setSourcesError(
          err instanceof Error ? err.message : "Could not load sources",
        );
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
      latestSequenceRef.current = Math.max(
        latestSequenceRef.current,
        event.sequence_no,
      );
      setItems((current) =>
        withAssistantParent(
          applyRuntimeEvent(current, event),
          event.run_id,
          activeRunUserMessageIdsRef.current.get(event.run_id) ?? null,
        ),
      );
      setCitations((current) => applyCitationEvent(current, event));
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
        setActiveRunId(null);
        activeRunUserMessageIdsRef.current.delete(event.run_id);
        setStatus(statusForTerminalRunEvent(event));
        void refreshConversations();
      }
    },
    [refreshConversations, setSubagents, setDraftRegistry],
  );

  const startEventStream = useCallback(
    (runId: string, afterSequence: number) => {
      if (reconnectTimeoutRef.current !== null) {
        window.clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      streamRef.current?.close();
      streamRef.current = streamRunEvents({
        runId,
        afterSequence,
        identity,
        onEvent: handleEvent,
        onError: () => {
          streamRef.current?.close();
          streamRef.current = null;
          setStatus("Stream paused. Reconnecting...");
          reconnectTimeoutRef.current = window.setTimeout(() => {
            startEventStream(runId, latestSequenceRef.current);
          }, 750);
        },
        onProtocolError: (error) => {
          setStatus(error.message);
        },
      });
    },
    [handleEvent, identity],
  );

  useEffect(() => {
    if (
      !initialHistoryLoaded ||
      activeRunId !== null ||
      streamRef.current !== null
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
    startEventStream(pendingRunId, latestSequenceRef.current);
  }, [activeRunId, initialHistoryLoaded, items, startEventStream]);

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
        startEventStream(runId, latestSequenceRef.current);
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
      if (activeRunId !== null) {
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
        setLatestRunEvent(null);
        setShowConnectorSuggestions(false);
        setStatus(history.replayFailed ? historyReplayWarning : "Ready");
      } catch (err) {
        setStatus(errorMessage(err, "Could not open conversation"));
      }
    },
    [activeRunId, identity, loadHistoryItems],
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
            model: applyDepth(
              modelSelectionForId(demoModels, selectedModelId),
              depth,
            ),
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
        startEventStream(run.run_id, latestSequenceRef.current);
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
    await cancelRun(activeRunId, identity);
    if (reconnectTimeoutRef.current !== null) {
      window.clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    streamRef.current?.close();
    streamRef.current = null;
    setActiveRunId(null);
    setLatestRunEvent(null);
    setStatus("Cancelling...");
  }, [activeRunId, identity]);

  const onStartNewChat = useCallback(() => {
    streamRef.current?.close();
    streamRef.current = null;
    setActiveRunId(null);
    setConversationId(null);
    setItems([]);
    setCitations(emptyCitationRegistry());
    // PR 3.2 — clear workspace-pane data feeds so a stale conversation's
    // sources / drafts don't leak into the empty welcome state.
    setSourcesMap(emptySourceMap());
    setSourcesError(null);
    setLatestRunEvent(null);
    setShowConnectorSuggestions(false);
    setStatus("Ready");
  }, []);

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

  async function onMcpAuthDecision(
    approvalId: string,
    decision: ApprovalDecision,
  ): Promise<void> {
    if (pendingApprovalDecisionsRef.current.has(approvalId)) {
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

  // Citation chips resolve against the active run's registry. When no
  // run is active (history viewing) we fall back to the most recent
  // assistant message's runId so chips on archived turns still resolve.
  const activeCitations = useMemo(() => {
    const fallbackRunId =
      activeRunId ?? mostRecentAssistantRunId(items) ?? null;
    return citationsForRun(citations, fallbackRunId);
  }, [activeRunId, citations, items]);

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

  const threadListAdapter = useMemo<ExternalStoreThreadListAdapter>(() => {
    const threads: ExternalStoreThreadData<"regular">[] = [];
    const archivedThreads: ExternalStoreThreadData<"archived">[] = [];

    for (const conversation of conversations) {
      const thread = {
        id: conversation.conversation_id,
        remoteId: conversation.conversation_id,
        title: conversation.title ?? "Untitled chat",
      };
      if (conversation.status === "archived") {
        archivedThreads.push({ ...thread, status: "archived" });
      } else {
        threads.push({ ...thread, status: "regular" });
      }
    }

    if (
      conversationId !== null &&
      !threads.some((thread) => thread.id === conversationId) &&
      !archivedThreads.some((thread) => thread.id === conversationId)
    ) {
      threads.unshift({
        status: "regular",
        id: conversationId,
        remoteId: conversationId,
        title: currentTitle(conversations, conversationId),
      });
    }

    return {
      threadId: conversationId ?? undefined,
      isLoading: historyLoading,
      threads,
      archivedThreads,
      onSwitchToNewThread: onStartNewChat,
      onSwitchToThread: loadConversationById,
    };
  }, [
    conversationId,
    conversations,
    historyLoading,
    loadConversationById,
    onStartNewChat,
  ]);

  const attachmentAdapter = useMemo(
    () =>
      new CompositeAttachmentAdapter([
        new SimpleImageAttachmentAdapter(),
        new SimpleTextAttachmentAdapter(),
        new GenericFileAttachmentAdapter(),
      ]),
    [],
  );
  const dictationAdapter = useMemo(
    () =>
      typeof window !== "undefined" && WebSpeechDictationAdapter.isSupported()
        ? new WebSpeechDictationAdapter()
        : undefined,
    [],
  );
  const aui = useAui({
    suggestions: Suggestions(CHAT_PROMPT_SUGGESTIONS),
  });

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
  const activeConnectorGlyphs = useMemo(
    () =>
      activeConnectorsFromScopes(connectors.servers, conversationScopes.scopes),
    [connectors.servers, conversationScopes.scopes],
  );
  // PR 3.4 — projection of the workspace-installed catalog + per-chat
  // overrides into the four-state row vocabulary the popover renders.
  // Memoised so the popover doesn't re-mount its keyboard-nav handler
  // on every parent render.
  const connectorRows = useMemo(
    () => projectConnectors(connectors.servers, conversationScopes.scopes),
    [connectors.servers, conversationScopes.scopes],
  );
  const connectorsActiveCount = useMemo(
    () => activeConnectorCount(connectorRows),
    [connectorRows],
  );

  // PR 3.4 — single open-state owner for the per-chat ConnectorPopover.
  // The same popover serves the topbar pill (anchored down) and the
  // composer button (anchored up). Only one is open at a time.
  const [connectorsPopover, setConnectorsPopover] = useState<
    null | "topbar" | "composer"
  >(null);
  const topbarConnectorsRef = useRef<HTMLButtonElement | null>(null);
  const composerConnectorsRef = useRef<HTMLButtonElement | null>(null);
  const closeConnectorsPopover = useCallback(
    () => setConnectorsPopover(null),
    [],
  );
  const onTopbarConnectorsOpen = useCallback(
    () => setConnectorsPopover((prev) => (prev === "topbar" ? null : "topbar")),
    [],
  );
  const onComposerConnectorsOpen = useCallback(
    () =>
      setConnectorsPopover((prev) => (prev === "composer" ? null : "composer")),
    [],
  );
  // Close the popover when the conversation switches — the rows are
  // about to change underneath the user, and the per-chat scopes would
  // momentarily disagree with the open popover's state.
  useEffect(() => {
    setConnectorsPopover(null);
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
      />
    ),
    [
      closeConnectorsPopover,
      connectorRows,
      conversationScopes,
      connectors,
      onOpenSettings,
      currentConversation,
    ],
  );
  const selectedModel = useMemo(
    () => demoModels.find((model) => model.id === selectedModelId) ?? null,
    [selectedModelId],
  );
  const depthVisible = modelSupportsDepth(selectedModel);

  const runtime = useExternalStoreRuntime<ChatThreadMessage>({
    messages: threadMessages,
    convertMessage: (message) => message,
    setMessages: (messages) => setItems(threadMessagesToChatItems(messages)),
    isRunning: activeRunId !== null,
    onNew,
    onEdit,
    onReload: async (parentId) => {
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
          model: applyDepth(
            modelSelectionForId(demoModels, selectedModelId),
            depth,
          ),
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
      startEventStream(run.run_id, latestSequenceRef.current);
    },
    onResumeToolCall: ({ payload }) => {
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
    onCancel,
    adapters: {
      attachments: attachmentAdapter,
      dictation: dictationAdapter,
      threadList: threadListAdapter,
    },
  });

  return (
    <AssistantRuntimeProvider runtime={runtime} aui={aui}>
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
            activeRunId={activeRunId}
            activeConversationId={conversationId}
            collapsed={sidebarCollapsed}
            conversations={conversations}
            loading={historyLoading}
            onOpenSettings={() => onOpenSettings("general")}
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
          />
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
                connectors={activeConnectorGlyphs}
                connectorsOpen={connectorsPopover === "topbar"}
                onOpenConnectors={onTopbarConnectorsOpen}
                connectorsTriggerRef={topbarConnectorsRef}
                connectorsPopover={
                  connectorsPopover === "topbar"
                    ? renderConnectorPopover(topbarConnectorsRef, "down")
                    : null
                }
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
                      typeof window !== "undefined" ? window.location.href : ""
                    }
                    conversationId={conversationId}
                    identity={identity}
                    onStatus={(message) => setStatus(message)}
                  />
                }
                onOpenSettings={() => onOpenSettings("general")}
              />
            }
          >
            <CitationsProvider
              citations={activeCitations}
              byRun={citations}
              terminalRuns={terminalRuns}
            >
              <ThreadBody
                connectors={connectors}
                skills={skills}
                onMcpAuthConnect={onMcpAuthConnect}
                onMcpAuthSkip={onMcpAuthSkip}
                onOpenMcpSettings={() => onOpenSettings("connectors")}
                onOpenSkillsSettings={() => onOpenSettings("skills")}
                onShowConnectors={() => setShowConnectorSuggestions(true)}
                onOpenDetailsPanel={(kind) => setDetailsPanel(kind)}
                onOpenSources={(citationId) =>
                  paneState.openOn("sources", { focusCitationId: citationId })
                }
                runIndicator={runIndicator}
                connectorsTrigger={
                  <span className="atlas-connectors-anchor atlas-connectors-anchor--composer">
                    <ComposerConnectorsButton
                      ref={composerConnectorsRef}
                      activeCount={connectorsActiveCount}
                      open={connectorsPopover === "composer"}
                      onClick={onComposerConnectorsOpen}
                      disabled={currentConversation === null}
                    />
                    {connectorsPopover === "composer"
                      ? renderConnectorPopover(composerConnectorsRef, "up")
                      : null}
                  </span>
                }
                connectorSuggestions={
                  showConnectorSuggestions && suggestedServers.length > 0 ? (
                    <ConnectorSuggestionCard
                      servers={suggestedServers}
                      onConnect={(serverId) =>
                        void connectors.authenticate(serverId)
                      }
                      onSkip={(serverId) => void connectors.skipAuth(serverId)}
                      onNone={() => setShowConnectorSuggestions(false)}
                    />
                  ) : null
                }
              />
            </CitationsProvider>
          </AssistantThread>
          <WorkspacePane
            state={paneState}
            sources={sourcesMap}
            sourcesLoading={sourcesLoading}
            sourcesError={sourcesError}
            subagents={subagentsState.subagents}
            subagentsLoading={subagentsState.loading}
            subagentsError={subagentsState.error}
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
              const composer = aui.composer();
              const current = composer.getState().text.trimEnd();
              composer.setText(
                current ? `${current} /${skill.name} ` : `/${skill.name} `,
              );
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
              citations={activeCitations}
              onClose={() => setDetailsPanel(null)}
            />
          ) : null}
        </main>
      </ApprovalFocusProvider>
    </AssistantRuntimeProvider>
  );
}

class GenericFileAttachmentAdapter implements AttachmentAdapter {
  public accept =
    "application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-powerpoint,application/vnd.openxmlformats-officedocument.presentationml.presentation,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx";

  public async add({ file }: { file: File }): Promise<PendingAttachment> {
    return {
      id: `${file.name}-${file.lastModified}`,
      type: "file",
      name: file.name,
      contentType: file.type || mimeTypeForFile(file.name),
      file,
      status: { type: "requires-action", reason: "composer-send" },
    };
  }

  public async send(
    attachment: PendingAttachment,
  ): Promise<CompleteAttachment> {
    return {
      ...attachment,
      status: { type: "complete" },
      content: [
        {
          type: "file",
          filename: attachment.name,
          data: await readFileDataURL(attachment.file),
          mimeType:
            attachment.contentType ||
            mimeTypeForFile(attachment.name) ||
            "application/octet-stream",
        },
      ],
    };
  }

  public async remove(_attachment: Attachment): Promise<void> {
    return undefined;
  }
}

function readFileDataURL(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = (error) => reject(error);
    reader.readAsDataURL(file);
  });
}

function mimeTypeForFile(fileName: string): string {
  const extension = fileName.split(".").pop()?.toLowerCase();
  switch (extension) {
    case "pdf":
      return "application/pdf";
    case "doc":
      return "application/msword";
    case "docx":
      return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
    case "xls":
      return "application/vnd.ms-excel";
    case "xlsx":
      return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
    case "ppt":
      return "application/vnd.ms-powerpoint";
    case "pptx":
      return "application/vnd.openxmlformats-officedocument.presentationml.presentation";
    default:
      return "";
  }
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

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
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
    return {
      type: "file",
      filename: part.filename,
      data: part.data,
      mime_type: part.mimeType,
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
    const hasPendingAction = item.content.some(
      (part) =>
        part.type === "tool-call" &&
        (part.toolName === "approval_request" ||
          part.toolName === "mcp_auth_required") &&
        part.result === undefined,
    );
    if (hasPendingAction) {
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
