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
  CompositeAttachmentAdapter,
  SimpleImageAttachmentAdapter,
  SimpleTextAttachmentAdapter,
  Suggestions,
  WebSpeechDictationAdapter,
  WebSpeechSynthesisAdapter,
  useAui,
  useExternalStoreRuntime,
  type AppendMessage,
  type CompleteAttachment,
  type ExternalStoreThreadData,
  type ExternalStoreThreadListAdapter,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import {
  Button,
  Card,
  Field,
  Select,
  useTheme,
  type ThemeScheme,
} from "@enterprise-search/design-system";
import type { ReactElement } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  cancelRun,
  createConversation,
  createRun,
  decideApproval,
  getConversation,
  listConversations,
  listModels,
  listMessages,
  replayRunEvents,
  streamRunEvents,
} from "../../api/agentApi";
import type { RequestIdentity } from "../../api/config";
import { ConnectorSuggestionCard } from "../connectors/ConnectorConsentCard";
import type { ConnectorState } from "../connectors/useConnectors";
import {
  applyRuntimeEvent,
  chatItemsToThreadMessages,
  messagesToChatItems,
  optimisticUserMessage,
  resolveApprovalDecision,
  resolveMcpAuthSkip,
  threadMessagesToChatItems,
  type ChatItem,
  type ChatThreadMessage,
} from "./chatModel";
import {
  AssistantThread,
  AssistantThreadList,
  ThreadBody,
} from "./assistantUiComponents";

export function ChatScreen({
  connectors,
  onOpenSettings,
  identity,
  oauthStatus,
}: {
  connectors: ConnectorState;
  onOpenSettings: () => void;
  identity: RequestIdentity;
  oauthStatus: string | null;
}): ReactElement {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [items, setItems] = useState<ChatItem[]>([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [showConnectorSuggestions, setShowConnectorSuggestions] =
    useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [status, setStatus] = useState("Ready");
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [models, setModels] = useState<ModelCatalogModel[]>(fallbackModels);
  const [selectedModelId, setSelectedModelId] = useState(fallbackModels[0].id);
  const streamRef = useRef<EventSource | null>(null);
  const latestSequenceRef = useRef(0);
  const reconnectTimeoutRef = useRef<number | null>(null);
  const activeRunUserMessageIdsRef = useRef<Map<string, string>>(new Map());

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
    ): Promise<{ items: ChatItem[]; replayFailed: boolean }> => {
      const history = await listMessages(nextConversationId, identity);
      const replay = await replayEventsForMessages(history.messages, identity);
      return {
        items: messagesToChatItems(history.messages, replay.eventsByRunId),
        replayFailed: replay.replayFailed,
      };
    },
    [identity],
  );

  useEffect(() => {
    let cancelled = false;
    async function loadInitialConversation(): Promise<void> {
      try {
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
          setItems([]);
          setStatus("Ready");
          setHistoryError(null);
          return;
        }
        setConversationId(latest.conversation_id);
        const history = await loadHistoryItems(latest.conversation_id);
        if (!cancelled) {
          setItems(history.items);
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
    let cancelled = false;
    async function loadModelCatalog(): Promise<void> {
      try {
        const response = await listModels(identity);
        if (cancelled) {
          return;
        }
        setModels(
          response.models.length > 0 ? response.models : fallbackModels,
        );
        setSelectedModelId(
          response.default_model_id ??
            response.models[0]?.id ??
            fallbackModels[0].id,
        );
      } catch {
        if (!cancelled) {
          setModels(fallbackModels);
          setSelectedModelId(fallbackModels[0].id);
        }
      }
    }
    void loadModelCatalog();
    return () => {
      cancelled = true;
    };
  }, [identity]);

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
      const liveStatus = statusForRuntimeEvent(event);
      if (liveStatus) {
        setStatus(liveStatus);
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
        setStatus(event.event_type === "run_completed" ? "Ready" : "Stopped");
        void refreshConversations();
      }
    },
    [refreshConversations],
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
        setItems(history.items);
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
      options: {
        parentMessageId?: string | null;
        sourceMessageId?: string | null;
        branchId?: string | null;
      } = {},
    ): Promise<void> => {
      const text = textFromAppendMessage(message).trim();
      if (!text || activeRunId !== null) {
        return;
      }

      let targetConversationId = conversationId;
      const localMessageId = appendMessageId(message) ?? `local-${Date.now()}`;
      const content = contentFromAppendMessage(message);
      const attachments = attachmentsFromAppendMessage(message);
      const quote = quoteFromAppendMessage(message);
      const parentMessageId =
        options.parentMessageId === undefined
          ? lastMessageId(items)
          : options.parentMessageId;
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
        const run = await createRun(targetConversationId, text, identity, {
          model: modelSelectionForId(models, selectedModelId),
          attachments,
          content,
          quote,
          parentMessageId,
          sourceMessageId: options.sourceMessageId,
          branchId: options.branchId,
        });
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
      identity,
      items,
      models,
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
    setStatus("Cancelling...");
  }, [activeRunId, identity]);

  const onStartNewChat = useCallback(() => {
    if (activeRunId !== null) {
      return;
    }
    setConversationId(null);
    setItems([]);
    setShowConnectorSuggestions(false);
    setStatus("Ready");
  }, [activeRunId]);

  async function onApprovalDecision(
    approvalId: string,
    decision: ApprovalDecision,
  ): Promise<void> {
    try {
      await decideApproval(approvalId, decision, identity);
      setItems((current) =>
        resolveApprovalDecision(current, approvalId, decision),
      );
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
    }
  }

  const onMcpAuthConnect = useCallback(
    async (serverId: string): Promise<void> => {
      try {
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
    async (serverId: string): Promise<void> => {
      try {
        await connectors.skipAuth(serverId);
        setItems((current) => resolveMcpAuthSkip(current, serverId));
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

  const threadMessages = useMemo<ChatThreadMessage[]>(
    () => chatItemsToThreadMessages(items, activeRunId),
    [activeRunId, items],
  );

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
  const speechAdapter = useMemo(() => new WebSpeechSynthesisAdapter(), []);
  const aui = useAui({
    suggestions: Suggestions([
      {
        title: "Search connectors",
        label: "Find context across connected apps",
        prompt:
          "Search connected apps for relevant context and summarize the findings.",
      },
      {
        title: "Think through risks",
        label: "Show reasoning and tool usage",
        prompt:
          "Think through the main risks, use available tools, and explain the recommendation.",
      },
      {
        title: "Call a subagent",
        label: "Delegate research",
        prompt:
          "Call a research subagent to investigate this and report back with sources.",
      },
    ]),
  });

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
          "Regenerate the previous response.",
        identity,
        {
          model: modelSelectionForId(models, selectedModelId),
          parentMessageId,
          sourceMessageId,
          regenerateFromMessageId: sourceMessageId ?? parentMessageId,
          branchId: nextBranchId(),
        },
      );
      activeRunUserMessageIdsRef.current.set(run.run_id, run.user_message_id);
      latestSequenceRef.current = 0;
      setActiveRunId(run.run_id);
      setStatus("Queued...");
      startEventStream(run.run_id, latestSequenceRef.current);
    },
    onResumeToolCall: ({ toolCallId, payload }) => {
      if (isApprovalResumePayload(payload)) {
        void onApprovalDecision(toolCallId, payload.decision);
      }
    },
    onCancel,
    adapters: {
      attachments: attachmentAdapter,
      dictation: dictationAdapter,
      speech: speechAdapter,
      threadList: threadListAdapter,
    },
  });

  return (
    <AssistantRuntimeProvider runtime={runtime} aui={aui}>
      <main className="aui-workspace">
        <AssistantThreadList
          activeRunId={activeRunId}
          conversations={conversations}
          loading={historyLoading}
          onRefresh={() => void refreshConversations()}
        />
        <AssistantThread
          title={currentTitle(conversations, conversationId)}
          status={historyError ?? status}
          models={models}
          selectedModel={selectedModelId}
          onModelChange={setSelectedModelId}
          modelDisabled={activeRunId !== null}
          onOpenSettings={() => setSettingsOpen(true)}
          onShowConnectors={() => setShowConnectorSuggestions(true)}
        >
          <ThreadBody
            oauthStatus={oauthStatus}
            onMcpAuthConnect={onMcpAuthConnect}
            onMcpAuthSkip={onMcpAuthSkip}
            connectorSuggestions={
              showConnectorSuggestions && suggestedServers.length > 0 ? (
                <ConnectorSuggestionCard
                  servers={suggestedServers}
                  onConnect={(serverId) => void onMcpAuthConnect(serverId)}
                  onSkip={(serverId) => void onMcpAuthSkip(serverId)}
                  onNone={() => setShowConnectorSuggestions(false)}
                />
              ) : null
            }
          />
        </AssistantThread>

        {settingsOpen ? (
          <ChatSettingsPanel
            connectors={connectors}
            identity={identity}
            onClose={() => setSettingsOpen(false)}
            onOpenFullSettings={() => {
              setSettingsOpen(false);
              onOpenSettings();
            }}
          />
        ) : null}
      </main>
    </AssistantRuntimeProvider>
  );
}

function ChatSettingsPanel({
  connectors,
  identity,
  onClose,
  onOpenFullSettings,
}: {
  connectors: ConnectorState;
  identity: RequestIdentity;
  onClose: () => void;
  onOpenFullSettings: () => void;
}): ReactElement {
  const { scheme, setScheme } = useTheme();
  const connectedCount = connectors.servers.filter(
    (server) => server.auth_state === "authenticated",
  ).length;
  const enabledCount = connectors.servers.filter(
    (server) => server.enabled,
  ).length;

  return (
    <aside className="chat-settings-panel" aria-label="Chat settings">
      <header>
        <div>
          <span className="app-eyebrow">Settings</span>
          <h2>Chat controls</h2>
        </div>
        <button
          className="aui-icon-button"
          aria-label="Close chat settings"
          type="button"
          onClick={onClose}
        >
          x
        </button>
      </header>
      <Card>
        <Field label="Theme" hint="Applies across chat and settings.">
          <Select
            value={scheme}
            onChange={(event) => setScheme(event.target.value as ThemeScheme)}
          >
            <option value="dark">Dark</option>
            <option value="light">Light</option>
            <option value="slate">Slate</option>
          </Select>
        </Field>
      </Card>
      <Card>
        <h3>Connectors</h3>
        <p>
          {connectedCount}/{enabledCount} enabled connectors authenticated.
        </p>
        <div className="chat-settings-panel__actions">
          <Button
            type="button"
            variant="secondary"
            onClick={() => void connectors.refresh()}
          >
            Refresh
          </Button>
          <Button
            type="button"
            variant="secondary"
            onClick={onOpenFullSettings}
          >
            Manage connectors
          </Button>
        </div>
      </Card>
      <Card>
        <h3>Session</h3>
        <p>
          Org <code>{identity.orgId}</code>
        </p>
        <p>
          User <code>{identity.userId}</code>
        </p>
      </Card>
    </aside>
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
      messages.flatMap((message) =>
        message.role === "assistant" && message.run_id ? [message.run_id] : [],
      ),
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
  return message.content.map((part) => ({
    ...part,
  })) as NonNullable<CreateRunRequest["content"]>;
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
    content: attachment.content as NonNullable<
      CreateRunRequest["attachments"]
    >[number]["content"],
  }));
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

function isApprovalResumePayload(
  payload: unknown,
): payload is { decision: ApprovalDecision; approval_id: string } {
  if (!payload || typeof payload !== "object") {
    return false;
  }
  const record = payload as Record<string, unknown>;
  return record.decision === "approved" || record.decision === "rejected";
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

function statusForRuntimeEvent(event: RuntimeEventEnvelope): string | null {
  if (event.visibility === "internal") {
    return null;
  }
  if (event.event_type === "run_started") {
    return "Working...";
  }
  if (event.event_type === "model_delta") {
    return "Writing answer...";
  }
  if (
    event.event_type === "reasoning_summary" ||
    event.event_type === "reasoning_summary_delta"
  ) {
    return "Thinking...";
  }
  return null;
}

const fallbackModels: ModelCatalogModel[] = [
  {
    id: "gpt-4.1-mini",
    provider: "openai",
    model_name: "gpt-4.1-mini",
    name: "GPT-4.1 Mini",
    description: "Default fast OpenAI model",
    configured: true,
  },
  {
    id: "claude-opus-4-7",
    provider: "anthropic",
    model_name: "claude-opus-4-7",
    name: "Claude Opus 4.7",
    description: "Anthropic reasoning model",
    configured: false,
  },
  {
    id: "gemini-2.5-pro",
    provider: "gemini",
    model_name: "gemini-2.5-pro",
    name: "Gemini 2.5 Pro",
    description: "Google long-context model",
    configured: false,
  },
];

const historyReplayWarning =
  "History loaded; some activity could not be restored.";
