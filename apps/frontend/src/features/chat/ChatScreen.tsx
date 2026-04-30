import type {
  ApprovalDecision,
  RuntimeEventEnvelope,
} from "@enterprise-search/api-types";
import {
  Button,
  ChatBubble,
  ChatComposer,
  ChatShell,
  ChatThread,
  DropdownMenu,
  IconButton,
  Textarea,
} from "@enterprise-search/design-system";
import type { FormEvent, ReactElement } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Streamdown } from "streamdown";
import {
  cancelRun,
  createConversation,
  createRun,
  decideApproval,
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
  const [items, setItems] = useState<ChatItem[]>([]);
  const [draft, setDraft] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [showConnectorSuggestions, setShowConnectorSuggestions] =
    useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [status, setStatus] = useState("Ready");
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

  useEffect(() => {
    let cancelled = false;
    async function loadConversation(): Promise<void> {
      try {
        setStatus("Opening conversation...");
        const conversation = await createConversation(identity);
        if (cancelled) {
          return;
        }
        setConversationId(conversation.conversation_id);
        const history = await listMessages(
          conversation.conversation_id,
          identity,
        );
        if (!cancelled) {
          setItems(messagesToChatItems(history.messages));
          setStatus("Ready");
        }
      } catch (err) {
        if (!cancelled) {
          setStatus(errorMessage(err, "Could not open chat"));
        }
      }
    }

    void loadConversation();
    return () => {
      cancelled = true;
      if (reconnectTimeoutRef.current !== null) {
        window.clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      streamRef.current?.close();
    };
  }, [identity]);

  const handleEvent = useCallback((event: RuntimeEventEnvelope) => {
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
    }
  }, []);

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

  async function onSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const text = draft.trim();
    if (!text || conversationId === null || activeRunId !== null) {
      return;
    }
    setDraft("");
    setItems((current) => [...current, optimisticUserMessage(text)]);
    try {
      const run = await createRun(conversationId, text, identity);
      latestSequenceRef.current = 0;
      setActiveRunId(run.run_id);
      setStatus("Queued...");
      startEventStream(run.run_id, latestSequenceRef.current);
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
  }

  async function onCancel(): Promise<void> {
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
  }

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

  function renderItem(item: ChatItem): ReactElement {
    if (item.kind === "message") {
      const isStreamingAssistant =
        item.role === "assistant" && item.id === `assistant-${activeRunId}`;
      return (
        <ChatBubble key={item.id} role={item.role}>
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
        </ChatBubble>
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
        <ChatBubble key={item.id} role="system">
          Approval requested:{" "}
          {item.payload.message ??
            item.payload.reason ??
            item.payload.approval_id}
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
        </ChatBubble>
      );
    }
    return (
      <ChatBubble key={item.id} role="system">
        <strong>{item.title}</strong>
        {item.text ? `\n${item.text}` : ""}
      </ChatBubble>
    );
  }

  return (
    <ChatShell>
      <ChatThread>
        <header className="chat-header">
          <div>
            <span className="app-eyebrow">Current task review</span>
            <h1>Enterprise Search</h1>
          </div>
          <Button type="button" variant="secondary" onClick={onOpenSettings}>
            Settings
          </Button>
        </header>

        {oauthStatus ? (
          <ChatBubble role="system">{oauthStatus}</ChatBubble>
        ) : null}

        {items.length === 0 ? (
          <section className="chat-empty">
            <h2>Can you check my current tasks?</h2>
            <p>
              Ask the agent to search, reason, or use connectors. If a connector
              needs auth, you will see a consent card before leaving chat.
            </p>
          </section>
        ) : (
          items.map(renderItem)
        )}

        {showConnectorSuggestions && suggestedServers.length > 0 ? (
          <ConnectorSuggestionCard
            servers={suggestedServers}
            onConnect={(serverId) => void connectors.authenticate(serverId)}
            onSkip={(serverId) => void connectors.skipAuth(serverId)}
            onNone={() => setShowConnectorSuggestions(false)}
          />
        ) : null}
      </ChatThread>

      <ChatComposer onSubmit={(event) => void onSubmit(event)}>
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
              onOpenSettings();
              setMenuOpen(false);
            }}
          >
            Open settings
          </button>
        </DropdownMenu>
        <Textarea
          aria-label="Message"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Reply..."
          rows={1}
        />
        {activeRunId ? (
          <Button
            type="button"
            variant="secondary"
            onClick={() => void onCancel()}
          >
            Stop
          </Button>
        ) : (
          <Button
            type="submit"
            disabled={!draft.trim() || conversationId === null}
          >
            Send
          </Button>
        )}
        <div className="composer-status">{status}</div>
      </ChatComposer>
    </ChatShell>
  );
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
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
