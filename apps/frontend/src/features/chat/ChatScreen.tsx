import type {
  ApprovalDecision,
  Conversation,
  RuntimeEventEnvelope,
} from "@enterprise-search/api-types";
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  ThreadPrimitive,
  useExternalStoreRuntime,
  type AppendMessage,
  type ExternalStoreThreadListAdapter,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import {
  Badge,
  Button,
  Card,
  DropdownMenu,
  Field,
  IconButton,
  Select,
  useTheme,
  type ThemeScheme,
} from "@enterprise-search/design-system";
import type { ReactElement } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Streamdown } from "streamdown";
import {
  cancelRun,
  createConversation,
  createRun,
  decideApproval,
  getConversation,
  listConversations,
  listMessages,
  streamRunEvents,
} from "../../api/agentApi";
import type { RequestIdentity } from "../../api/config";
import {
  ConnectorConsentCard,
  ConnectorSuggestionCard,
} from "../connectors/ConnectorConsentCard";
import type { ConnectorState } from "../connectors/useConnectors";
import {
  applyRuntimeEvent,
  messagesToChatItems,
  optimisticUserMessage,
  type ChatItem,
} from "./chatModel";
import { RunActivityPanel } from "./RunActivityPanel";

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
  const [menuOpen, setMenuOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [showConnectorSuggestions, setShowConnectorSuggestions] =
    useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [status, setStatus] = useState("Ready");
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
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
        const run = await createRun(targetConversationId, text, identity);
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
      refreshConversations,
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
        current.map((item) =>
          item.kind === "approval" && item.payload.approval_id === approvalId
            ? {
                id: `approval-${approvalId}-${decision}`,
                kind: "status",
                title: "Approval resolved",
                text: decision === "approved" ? "Approved." : "Rejected.",
              }
            : item,
        ),
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
    () =>
      items
        .filter((item): item is Extract<ChatItem, { kind: "message" }> => {
          return item.kind === "message";
        })
        .map((item) => {
          const message = {
            id: item.id,
            role: item.role,
            content: [{ type: "text" as const, text: item.text }],
          };
          if (
            item.role === "assistant" &&
            item.id === `assistant-${activeRunId}`
          ) {
            return { ...message, status: { type: "running" as const } };
          }
          return message;
        }),
    [activeRunId, items],
  );

  const threadListAdapter = useMemo<ExternalStoreThreadListAdapter>(
    () => ({
      threadId: conversationId ?? "new-chat",
      isLoading: historyLoading,
      threads: conversations
        .filter((conversation) => conversation.status !== "archived")
        .map((conversation) => ({
          status: "regular",
          id: conversation.conversation_id,
          remoteId: conversation.conversation_id,
          title: conversation.title ?? "Untitled chat",
        })),
      archivedThreads: conversations
        .filter((conversation) => conversation.status === "archived")
        .map((conversation) => ({
          status: "archived",
          id: conversation.conversation_id,
          remoteId: conversation.conversation_id,
          title: conversation.title ?? "Untitled chat",
        })),
      onSwitchToNewThread: onStartNewChat,
      onSwitchToThread: loadConversationById,
    }),
    [
      conversationId,
      conversations,
      historyLoading,
      loadConversationById,
      onStartNewChat,
    ],
  );

  const runtime = useExternalStoreRuntime<ThreadMessageLike>({
    messages: threadMessages,
    convertMessage: (message) => message,
    isRunning: activeRunId !== null,
    onNew,
    onCancel,
    adapters: {
      threadList: threadListAdapter,
    },
  });

  function renderItem(item: ChatItem): ReactElement {
    if (item.kind === "message") {
      const isStreamingAssistant =
        item.role === "assistant" && item.id === `assistant-${activeRunId}`;
      return (
        <article
          key={item.id}
          className={`chat-message chat-message--${item.role}`}
        >
          <div className="chat-message__meta">
            {item.role === "assistant"
              ? "Enterprise Search"
              : item.role === "user"
                ? "You"
                : "System"}
          </div>
          <div className="chat-message__content">
            {item.role === "assistant" ? (
              <Streamdown
                className="assistant-markdown"
                mode={isStreamingAssistant ? "streaming" : "static"}
              >
                {item.text}
              </Streamdown>
            ) : (
              item.text
            )}
          </div>
        </article>
      );
    }
    if (item.kind === "run-activity") {
      return <RunActivityPanel key={item.id} activity={item.activity} />;
    }
    if (item.kind === "mcp-auth") {
      return (
        <ConnectorConsentCard
          key={item.id}
          payload={item.payload}
          onSkip={(serverId) => void connectors.skipAuth(serverId)}
        />
      );
    }
    if (item.kind === "approval") {
      return (
        <Card key={item.id} tone="accent" className="approval-card">
          <strong>Approval requested</strong>
          <p>
            {item.payload.message ??
              item.payload.reason ??
              item.payload.approval_id}
          </p>
          <div className="approval-actions">
            <Button
              type="button"
              variant="secondary"
              onClick={() =>
                void onApprovalDecision(item.payload.approval_id, "approved")
              }
            >
              Approve
            </Button>
            <Button
              type="button"
              variant="danger"
              onClick={() =>
                void onApprovalDecision(item.payload.approval_id, "rejected")
              }
            >
              Reject
            </Button>
          </div>
        </Card>
      );
    }
    return (
      <article key={item.id} className="chat-message chat-message--system">
        <strong>{item.title}</strong>
        {item.text ? `\n${item.text}` : ""}
      </article>
    );
  }

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <main className="chat-workspace">
        <HistorySidebar
          activeRunId={activeRunId}
          conversations={conversations}
          currentConversationId={conversationId}
          error={historyError}
          loading={historyLoading}
          onNewChat={onStartNewChat}
          onRefresh={() => void refreshConversations()}
          onSelect={(nextConversationId) =>
            void loadConversationById(nextConversationId)
          }
        />
        <section className="assistant-chat-shell">
          <header className="chat-header">
            <div>
              <span className="app-eyebrow">AI work surface</span>
              <h1>{currentTitle(conversations, conversationId)}</h1>
            </div>
            <div className="chat-header__actions">
              <Badge tone={activeRunId ? "accent" : "neutral"}>{status}</Badge>
              <Button
                type="button"
                variant="secondary"
                onClick={() => setSettingsOpen(true)}
              >
                Chat settings
              </Button>
            </div>
          </header>

          <ThreadPrimitive.Root className="assistant-thread">
            <ThreadPrimitive.Viewport className="assistant-thread__viewport">
              {oauthStatus ? (
                <article className="chat-message chat-message--system">
                  {oauthStatus}
                </article>
              ) : null}

              {items.length === 0 ? (
                <section className="chat-empty">
                  <span className="app-eyebrow">Ready when you are</span>
                  <h2>What should Enterprise Search help with?</h2>
                  <p>
                    Ask the agent to search, reason, or use connectors. Thinking
                    updates, tool calls, and approvals will stay visible in the
                    thread.
                  </p>
                </section>
              ) : (
                items.map(renderItem)
              )}

              {showConnectorSuggestions && suggestedServers.length > 0 ? (
                <ConnectorSuggestionCard
                  servers={suggestedServers}
                  onConnect={(serverId) =>
                    void connectors.authenticate(serverId)
                  }
                  onSkip={(serverId) => void connectors.skipAuth(serverId)}
                  onNone={() => setShowConnectorSuggestions(false)}
                />
              ) : null}
              <ThreadPrimitive.ViewportFooter />
            </ThreadPrimitive.Viewport>
          </ThreadPrimitive.Root>

          <ComposerPrimitive.Root className="assistant-composer">
            <DropdownMenu
              open={menuOpen}
              trigger={
                <IconButton
                  label="Open composer actions"
                  type="button"
                  onClick={() => setMenuOpen((open) => !open)}
                >
                  +
                </IconButton>
              }
            >
              <button
                type="button"
                onClick={() => {
                  setShowConnectorSuggestions(true);
                  setMenuOpen(false);
                }}
              >
                Choose connectors
              </button>
              <button
                type="button"
                onClick={() => {
                  setSettingsOpen(true);
                  setMenuOpen(false);
                }}
              >
                Chat settings
              </button>
              <button
                type="button"
                onClick={() => {
                  onOpenSettings();
                  setMenuOpen(false);
                }}
              >
                Full settings
              </button>
            </DropdownMenu>
            <ComposerPrimitive.Input
              aria-label="Message"
              className="assistant-composer__input"
              maxRows={8}
              minRows={1}
              placeholder="Message Enterprise Search..."
              submitMode="enter"
            />
            {activeRunId ? (
              <ComposerPrimitive.Cancel className="ui-button ui-button--secondary ui-button--md">
                Stop
              </ComposerPrimitive.Cancel>
            ) : (
              <ComposerPrimitive.Send className="ui-button ui-button--primary ui-button--md">
                Send
              </ComposerPrimitive.Send>
            )}
            <div className="composer-status">{status}</div>
          </ComposerPrimitive.Root>
        </section>

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

function HistorySidebar({
  activeRunId,
  conversations,
  currentConversationId,
  error,
  loading,
  onNewChat,
  onRefresh,
  onSelect,
}: {
  activeRunId: string | null;
  conversations: Conversation[];
  currentConversationId: string | null;
  error: string | null;
  loading: boolean;
  onNewChat: () => void;
  onRefresh: () => void;
  onSelect: (conversationId: string) => void;
}): ReactElement {
  const disabled = activeRunId !== null;
  return (
    <aside className="history-sidebar" aria-label="Conversation history">
      <div className="history-sidebar__top">
        <div>
          <span className="app-eyebrow">History</span>
          <h2>Chats</h2>
        </div>
        <IconButton
          label="Refresh conversations"
          type="button"
          onClick={onRefresh}
        >
          R
        </IconButton>
      </div>
      <Button
        type="button"
        variant="secondary"
        className="history-sidebar__new"
        disabled={disabled}
        onClick={onNewChat}
      >
        New chat
      </Button>
      {loading ? (
        <p className="history-sidebar__note">Loading history...</p>
      ) : null}
      {error ? <p className="app-error">{error}</p> : null}
      <nav className="history-list" aria-label="Previous chats">
        {conversations.length === 0 && !loading ? (
          <p className="history-sidebar__note">No chats yet.</p>
        ) : null}
        {conversations.map((conversation) => (
          <button
            key={conversation.conversation_id}
            type="button"
            className={
              conversation.conversation_id === currentConversationId
                ? "history-list__item is-active"
                : "history-list__item"
            }
            disabled={disabled}
            onClick={() => onSelect(conversation.conversation_id)}
          >
            <strong>{conversation.title ?? "Untitled chat"}</strong>
            <span>{formatHistoryDate(conversation.updated_at)}</span>
          </button>
        ))}
      </nav>
    </aside>
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
        <IconButton label="Close chat settings" type="button" onClick={onClose}>
          x
        </IconButton>
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

function formatHistoryDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Recently";
  }
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
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
