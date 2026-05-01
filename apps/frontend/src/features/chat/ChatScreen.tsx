import type {
  ApprovalDecision,
  Conversation,
  CreateRunRequest,
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
  type ChatItem,
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
        const history = await listMessages(latest.conversation_id, identity);
        if (!cancelled) {
          setItems(messagesToChatItems(history.messages));
          setStatus("Ready");
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
  }, [identity]);

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
      setItems((current) => applyRuntimeEvent(current, event));
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
          listMessages(nextConversationId, identity),
        ]);
        setConversationId(nextConversationId);
        setConversations((current) =>
          upsertConversation(current, conversation),
        );
        setItems(messagesToChatItems(history.messages));
        setShowConnectorSuggestions(false);
        setStatus("Ready");
      } catch (err) {
        setStatus(errorMessage(err, "Could not open conversation"));
      }
    },
    [activeRunId, identity],
  );

  const onNew = useCallback(
    async (message: AppendMessage): Promise<void> => {
      const text = textFromAppendMessage(message).trim();
      if (!text || activeRunId !== null) {
        return;
      }

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

        setItems((current) => [...current, optimisticUserMessage(text)]);
        const run = await createRun(targetConversationId, text, identity, {
          model: modelSelectionForId(models, selectedModelId),
          attachments: attachmentsFromAppendMessage(message),
          content: contentFromAppendMessage(message),
          quote: quoteFromAppendMessage(message),
        });
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
      models,
      refreshConversations,
      selectedModelId,
      startEventStream,
    ],
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

  const threadMessages = useMemo<ThreadMessageLike[]>(
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

  const runtime = useExternalStoreRuntime<ThreadMessageLike>({
    messages: threadMessages,
    convertMessage: (message) => message,
    isRunning: activeRunId !== null,
    onNew,
    onEdit: onNew,
    onReload: async (parentId) => {
      if (activeRunId !== null || conversationId === null) {
        return;
      }
      const run = await createRun(
        conversationId,
        "Regenerate the previous response.",
        identity,
        {
          model: modelSelectionForId(models, selectedModelId),
          regenerateFromMessageId: parentId ?? undefined,
        },
      );
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

function attachmentsFromAppendMessage(
  message: AppendMessage,
): NonNullable<CreateRunRequest["attachments"]> {
  const attachments = message.attachments ?? [];
  return attachments.map((attachment: CompleteAttachment) => ({
    id: attachment.id,
    type: attachment.type,
    name: attachment.name,
    content_type: attachment.contentType ?? null,
    content: attachment.content,
  }));
}

function quoteFromAppendMessage(
  message: AppendMessage,
): Record<string, unknown> | undefined {
  const quote = message.metadata.custom.quote;
  return quote && typeof quote === "object"
    ? (quote as Record<string, unknown>)
    : undefined;
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
    return event.parent_task_id ? "Subagent working..." : null;
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
    return event.parent_task_id || event.subagent_id
      ? "Subagent thinking..."
      : "Thinking...";
  }
  if (
    event.event_type === "tool_call" ||
    event.event_type === "tool_call_started" ||
    event.event_type === "tool_call_delta"
  ) {
    const toolName = stringPayload(event.payload, "tool_name") ?? "tool";
    return `Using ${toolName}...`;
  }
  if (
    event.event_type === "tool_result" ||
    event.event_type === "tool_call_completed"
  ) {
    const toolName = stringPayload(event.payload, "tool_name") ?? "tool";
    return `${toolName} finished`;
  }
  if (
    event.event_type === "subagent_started" ||
    event.event_type === "subagent_progress" ||
    event.event_type === "subagent_update"
  ) {
    const subagentName =
      event.subagent_id ??
      stringPayload(event.payload, "subagent_name") ??
      "Subagent";
    return `${subagentName} working...`;
  }
  return null;
}

function stringPayload(
  payload: Record<string, unknown>,
  key: string,
): string | null {
  const value = payload[key];
  return typeof value === "string" && value.trim() ? value : null;
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
