import {
  ActionBarPrimitive,
  AuiIf,
  BranchPickerPrimitive,
  ComposerPrimitive,
  MessagePrimitive,
  SelectionToolbarPrimitive,
  SuggestionPrimitive,
  ThreadListItemPrimitive,
  ThreadListPrimitive,
  ThreadPrimitive,
  unstable_useMentionAdapter,
  unstable_useSlashCommandAdapter,
  type ReasoningGroupProps,
  type ReasoningMessagePartProps,
  type TextMessagePartProps,
  type ToolCallMessagePartProps,
  type Unstable_MentionCategory,
  type Unstable_SlashCommand,
} from "@assistant-ui/react";
import { Streamdown } from "streamdown";
import type { ReactElement, ReactNode } from "react";
import { useMemo } from "react";
import type {
  ApprovalDecision,
  Conversation,
  McpServer,
  ModelCatalogModel,
} from "@enterprise-search/api-types";

export function AssistantThreadList({
  conversations,
  loading,
  activeRunId,
  onRefresh,
}: {
  conversations: Conversation[];
  loading: boolean;
  activeRunId: string | null;
  onRefresh: () => void;
}): ReactElement {
  return (
    <aside className="aui-sidebar" aria-label="Conversation history">
      <div className="aui-sidebar__header">
        <div>
          <span className="aui-kicker">Assistant UI</span>
          <h1>Threads</h1>
        </div>
        <button
          className="aui-icon-button"
          type="button"
          aria-label="Refresh conversations"
          onClick={onRefresh}
        >
          R
        </button>
      </div>
      <ThreadListPrimitive.Root className="aui-thread-list">
        <ThreadListPrimitive.New
          className="aui-new-thread"
          disabled={activeRunId !== null}
        >
          New Thread
        </ThreadListPrimitive.New>
        {loading ? (
          <p className="aui-sidebar__note">Loading history...</p>
        ) : null}
        {!loading && conversations.length === 0 ? (
          <p className="aui-sidebar__note">No threads yet.</p>
        ) : null}
        <ThreadListPrimitive.Items>
          {() => (
            <ThreadListItemPrimitive.Root className="aui-thread-list-item">
              <ThreadListItemPrimitive.Trigger
                className="aui-thread-list-item__trigger"
                disabled={activeRunId !== null}
              >
                <ThreadListItemPrimitive.Title />
              </ThreadListItemPrimitive.Trigger>
            </ThreadListItemPrimitive.Root>
          )}
        </ThreadListPrimitive.Items>
      </ThreadListPrimitive.Root>
    </aside>
  );
}

export function ModelSelector({
  models,
  value,
  onChange,
  disabled,
}: {
  models: ModelCatalogModel[];
  value: string;
  onChange: (modelId: string) => void;
  disabled?: boolean;
}): ReactElement {
  const selected = models.find((model) => model.id === value) ?? models[0];
  return (
    <label className="aui-model-selector">
      <span className="sr-only">Select model</span>
      <select
        value={selected?.id ?? value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      >
        {models.map((model) => (
          <option key={model.id} value={model.id}>
            {model.name}
          </option>
        ))}
      </select>
      <span aria-hidden="true">⌄</span>
    </label>
  );
}

export function AssistantThread({
  title,
  status,
  models,
  selectedModel,
  onModelChange,
  modelDisabled,
  onOpenSettings,
  onShowConnectors,
  children,
}: {
  title: string;
  status: string;
  models: ModelCatalogModel[];
  selectedModel: string;
  onModelChange: (modelId: string) => void;
  modelDisabled?: boolean;
  onOpenSettings: () => void;
  onShowConnectors: () => void;
  children: ReactNode;
}): ReactElement {
  return (
    <section className="aui-chat-panel">
      <header className="aui-chat-header">
        <div>
          <span className="aui-kicker">Enterprise Search</span>
          <h2>{title}</h2>
        </div>
        <div className="aui-chat-header__actions">
          <ModelSelector
            models={models}
            value={selectedModel}
            onChange={onModelChange}
            disabled={modelDisabled}
          />
          <span className="aui-status-pill">{status}</span>
          <button
            className="aui-ghost-button"
            type="button"
            onClick={onShowConnectors}
          >
            Connectors
          </button>
          <button
            className="aui-ghost-button"
            type="button"
            onClick={onOpenSettings}
          >
            Settings
          </button>
        </div>
      </header>
      <div className="not-prose aui-demo-frame">{children}</div>
    </section>
  );
}

export function ThreadBody({
  oauthStatus,
  connectorSuggestions,
}: {
  oauthStatus: string | null;
  connectorSuggestions: ReactNode;
}): ReactElement {
  return (
    <ThreadPrimitive.Root className="aui-thread-root">
      <SelectionToolbarPrimitive.Root className="aui-selection-toolbar">
        <SelectionToolbarPrimitive.Quote className="aui-selection-toolbar__button">
          Quote
        </SelectionToolbarPrimitive.Quote>
      </SelectionToolbarPrimitive.Root>
      <ThreadPrimitive.Viewport className="aui-thread-viewport">
        {oauthStatus ? <SystemNotice>{oauthStatus}</SystemNotice> : null}
        <ThreadPrimitive.Empty>
          <ThreadWelcome />
        </ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages>
          {({ message }) => {
            if (message.role === "user") {
              return message.composer.isEditing ? (
                <UserEditComposer />
              ) : (
                <UserMessage />
              );
            }
            if (message.role === "system") {
              return <SystemMessage />;
            }
            return <AssistantMessage />;
          }}
        </ThreadPrimitive.Messages>
        {connectorSuggestions}
        <ThreadPrimitive.ViewportFooter className="aui-thread-footer">
          <ThreadPrimitive.ScrollToBottom className="aui-scroll-bottom">
            Scroll to bottom
          </ThreadPrimitive.ScrollToBottom>
          <AssistantComposer />
        </ThreadPrimitive.ViewportFooter>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
}

export function ThreadWelcome(): ReactElement {
  return (
    <section className="aui-welcome">
      <span className="aui-kicker">Ready when you are</span>
      <h2>What should Enterprise Search help with?</h2>
      <p>
        Ask the agent to search, reason, use connectors, run tools, or call
        subagents. Thinking, tool execution, and approvals will stream in the
        thread.
      </p>
      <div className="aui-suggestions">
        <ThreadPrimitive.Suggestions>
          {() => (
            <SuggestionPrimitive.Trigger className="aui-suggestion" send>
              <strong>
                <SuggestionPrimitive.Title />
              </strong>
              <span>
                <SuggestionPrimitive.Description />
              </span>
            </SuggestionPrimitive.Trigger>
          )}
        </ThreadPrimitive.Suggestions>
      </div>
    </section>
  );
}

export function AssistantComposer(): ReactElement {
  const slash = unstable_useSlashCommandAdapter({
    commands: useMemo<readonly Unstable_SlashCommand[]>(
      () => [
        {
          id: "summarize",
          label: "Summarize",
          description: "Ask for a concise summary.",
          execute: () => undefined,
        },
        {
          id: "connectors",
          label: "Use connectors",
          description: "Ask the agent to consider connected apps.",
          execute: () => undefined,
        },
        {
          id: "subagent",
          label: "Call a subagent",
          description: "Delegate research or implementation work.",
          execute: () => undefined,
        },
      ],
      [],
    ),
    removeOnExecute: false,
  });
  const mention = unstable_useMentionAdapter({
    categories: useMemo<readonly Unstable_MentionCategory[]>(
      () => [
        {
          id: "context",
          label: "Context",
          items: [
            {
              id: "current-thread",
              type: "context",
              label: "Current thread",
              description: "Reference this conversation.",
            },
            {
              id: "connectors",
              type: "context",
              label: "Connectors",
              description: "Reference enabled connectors.",
            },
          ],
        },
      ],
      [],
    ),
    includeModelContextTools: true,
  });
  return (
    <ComposerPrimitive.Unstable_TriggerPopoverRoot>
      <ComposerPrimitive.Root className="aui-composer">
        <ComposerPrimitive.AttachmentDropzone className="aui-composer__dropzone">
          <ComposerPrimitive.Quote className="aui-quote-preview">
            <ComposerPrimitive.QuoteText />
            <ComposerPrimitive.QuoteDismiss className="aui-icon-button">
              ×
            </ComposerPrimitive.QuoteDismiss>
          </ComposerPrimitive.Quote>
          <ComposerPrimitive.Attachments>
            {({ attachment }) => <AttachmentPill attachment={attachment} />}
          </ComposerPrimitive.Attachments>
          <div className="aui-composer__input-row">
            <ComposerPrimitive.AddAttachment
              className="aui-icon-button"
              multiple
              title="Attach files"
            >
              +
            </ComposerPrimitive.AddAttachment>
            <ComposerPrimitive.Input
              className="aui-composer__input"
              aria-label="Message"
              placeholder="Send a message... (@ to mention, / for commands)"
              minRows={1}
              maxRows={8}
              submitMode="enter"
            />
            <AuiIf condition={(state) => state.composer.dictation == null}>
              <ComposerPrimitive.Dictate
                className="aui-icon-button"
                title="Start voice dictation"
              >
                Mic
              </ComposerPrimitive.Dictate>
            </AuiIf>
            <AuiIf condition={(state) => state.composer.dictation != null}>
              <ComposerPrimitive.StopDictation
                className="aui-icon-button"
                title="Stop voice dictation"
              >
                Stop Mic
              </ComposerPrimitive.StopDictation>
            </AuiIf>
            <AuiIf condition={(state) => !state.thread.isRunning}>
              <ComposerPrimitive.Send className="aui-send-button">
                Send
              </ComposerPrimitive.Send>
            </AuiIf>
            <AuiIf condition={(state) => state.thread.isRunning}>
              <ComposerPrimitive.Cancel className="aui-send-button aui-send-button--stop">
                Stop
              </ComposerPrimitive.Cancel>
            </AuiIf>
          </div>
          <ComposerPrimitive.DictationTranscript className="aui-dictation-preview" />
        </ComposerPrimitive.AttachmentDropzone>
        <ComposerPrimitive.Unstable_TriggerPopover
          char="/"
          adapter={slash.adapter}
          className="aui-trigger-popover"
        >
          <ComposerPrimitive.Unstable_TriggerPopover.Action {...slash.action} />
          <TriggerPopoverList />
        </ComposerPrimitive.Unstable_TriggerPopover>
        <ComposerPrimitive.Unstable_TriggerPopover
          char="@"
          adapter={mention.adapter}
          className="aui-trigger-popover"
        >
          <ComposerPrimitive.Unstable_TriggerPopover.Directive
            {...mention.directive}
          />
          <TriggerPopoverList />
        </ComposerPrimitive.Unstable_TriggerPopover>
      </ComposerPrimitive.Root>
    </ComposerPrimitive.Unstable_TriggerPopoverRoot>
  );
}

function TriggerPopoverList(): ReactElement {
  return (
    <>
      <ComposerPrimitive.Unstable_TriggerPopoverCategories className="aui-trigger-popover__list">
        {(categories) =>
          categories.map((category) => (
            <ComposerPrimitive.Unstable_TriggerPopoverCategoryItem
              key={category.id}
              categoryId={category.id}
              className="aui-trigger-popover__item"
            >
              {category.label}
            </ComposerPrimitive.Unstable_TriggerPopoverCategoryItem>
          ))
        }
      </ComposerPrimitive.Unstable_TriggerPopoverCategories>
      <ComposerPrimitive.Unstable_TriggerPopoverItems className="aui-trigger-popover__list">
        {(items) =>
          items.map((item, index) => (
            <ComposerPrimitive.Unstable_TriggerPopoverItem
              key={item.id}
              item={item}
              index={index}
              className="aui-trigger-popover__item"
            >
              <strong>{item.label}</strong>
              {item.description ? <span>{item.description}</span> : null}
            </ComposerPrimitive.Unstable_TriggerPopoverItem>
          ))
        }
      </ComposerPrimitive.Unstable_TriggerPopoverItems>
      <ComposerPrimitive.Unstable_TriggerPopoverBack className="aui-trigger-popover__back">
        Back
      </ComposerPrimitive.Unstable_TriggerPopoverBack>
    </>
  );
}

function AttachmentPill({
  attachment,
}: {
  attachment: { name: string; type: string };
}): ReactElement {
  return (
    <span className="aui-attachment-pill">
      <span>{attachment.name}</span>
      <small>{attachment.type}</small>
    </span>
  );
}

function AssistantMessage(): ReactElement {
  return (
    <MessagePrimitive.Root className="aui-message aui-message--assistant">
      <div className="aui-message__avatar" aria-hidden="true">
        AI
      </div>
      <div className="aui-message__body">
        <MessagePrimitive.Parts
          components={{
            Text: MarkdownText,
            Reasoning,
            ReasoningGroup,
            ToolGroup,
            tools: {
              Fallback: ToolFallback,
              by_name: {
                run_subagent: SubagentTool,
                approval_request: ApprovalTool,
                mcp_auth_required: ConnectorAuthTool,
              },
            },
          }}
        />
        <AssistantActionBar />
        <MessageBranchPicker />
      </div>
    </MessagePrimitive.Root>
  );
}

function UserMessage(): ReactElement {
  return (
    <MessagePrimitive.Root className="aui-message aui-message--user">
      <div className="aui-message__body">
        <MessagePrimitive.Attachments>
          {({ attachment }) => <AttachmentPill attachment={attachment} />}
        </MessagePrimitive.Attachments>
        <MessagePrimitive.Parts components={{ Text: PlainText }} />
        <UserActionBar />
        <MessageBranchPicker />
      </div>
    </MessagePrimitive.Root>
  );
}

function UserEditComposer(): ReactElement {
  return (
    <MessagePrimitive.Root className="aui-message aui-message--user">
      <ComposerPrimitive.Root className="aui-edit-composer">
        <ComposerPrimitive.Input
          className="aui-composer__input"
          aria-label="Edit message"
          maxRows={8}
          submitMode="enter"
        />
        <div className="aui-edit-composer__actions">
          <ComposerPrimitive.Cancel className="aui-ghost-button">
            Cancel
          </ComposerPrimitive.Cancel>
          <ComposerPrimitive.Send className="aui-send-button">
            Save
          </ComposerPrimitive.Send>
        </div>
      </ComposerPrimitive.Root>
    </MessagePrimitive.Root>
  );
}

function SystemMessage(): ReactElement {
  return (
    <MessagePrimitive.Root className="aui-system-message">
      <MessagePrimitive.Parts components={{ Text: PlainText }} />
    </MessagePrimitive.Root>
  );
}

function SystemNotice({ children }: { children: ReactNode }): ReactElement {
  return <div className="aui-system-message">{children}</div>;
}

export function MarkdownText({ text }: TextMessagePartProps): ReactElement {
  return (
    <Streamdown className="assistant-markdown" mode="streaming">
      {text}
    </Streamdown>
  );
}

function PlainText({ text }: TextMessagePartProps): ReactElement {
  return <div className="aui-plain-text">{text}</div>;
}

function Reasoning({ text, status }: ReasoningMessagePartProps): ReactElement {
  return (
    <Streamdown
      className="reasoning-markdown"
      mode={status.type === "running" ? "streaming" : "static"}
    >
      {text}
    </Streamdown>
  );
}

function ReasoningGroup({ children }: ReasoningGroupProps): ReactElement {
  return (
    <details className="aui-reasoning-group" open>
      <summary>Thinking</summary>
      <div className="aui-reasoning-group__content">{children}</div>
    </details>
  );
}

function ToolGroup({
  children,
}: {
  startIndex: number;
  endIndex: number;
  children?: ReactNode;
}): ReactElement {
  return (
    <details className="aui-tool-group" open>
      <summary>Tool execution</summary>
      <div className="aui-tool-group__content">{children}</div>
    </details>
  );
}

function ToolFallback({
  toolName,
  argsText,
  result,
  status,
  isError,
}: ToolCallMessagePartProps): ReactElement {
  return (
    <div className="aui-tool-card" data-status={status.type}>
      <div className="aui-tool-card__header">
        <strong>{toolName}</strong>
        <span>{toolStatusLabel(status.type, isError)}</span>
      </div>
      {argsText ? <pre>{argsText}</pre> : null}
      {result !== undefined ? <pre>{formatToolValue(result)}</pre> : null}
    </div>
  );
}

function SubagentTool(props: ToolCallMessagePartProps): ReactElement {
  const data = asRecord(props.args);
  return (
    <div className="aui-tool-card aui-tool-card--subagent">
      <div className="aui-tool-card__header">
        <strong>{String(data.subagent_name ?? data.name ?? "Subagent")}</strong>
        <span>{toolStatusLabel(props.status.type, props.isError)}</span>
      </div>
      {typeof data.summary === "string" ? <p>{data.summary}</p> : null}
      {props.result !== undefined ? (
        <pre>{formatToolValue(props.result)}</pre>
      ) : null}
    </div>
  );
}

function ApprovalTool({
  args,
  result,
  status,
  addResult,
  resume,
}: ToolCallMessagePartProps<Record<string, unknown>>): ReactElement {
  const approvalId = String(args.approval_id ?? "");
  const resolved = result !== undefined || status.type === "complete";
  const submit = (decision: ApprovalDecision): void => {
    addResult({ decision, approval_id: approvalId });
    resume({ decision, approval_id: approvalId });
  };
  return (
    <div className="aui-tool-card aui-tool-card--approval">
      <div className="aui-tool-card__header">
        <strong>Approval requested</strong>
        <span>{resolved ? "resolved" : "waiting"}</span>
      </div>
      <p>{String(args.message ?? args.reason ?? approvalId)}</p>
      {!resolved ? (
        <div className="aui-tool-card__actions">
          <button type="button" onClick={() => submit("approved")}>
            Approve
          </button>
          <button type="button" onClick={() => submit("rejected")}>
            Reject
          </button>
        </div>
      ) : null}
    </div>
  );
}

function ConnectorAuthTool({
  args,
}: ToolCallMessagePartProps<Record<string, unknown>>): ReactElement {
  return (
    <div className="aui-tool-card aui-tool-card--connector">
      <div className="aui-tool-card__header">
        <strong>Connector access needed</strong>
        <span>action required</span>
      </div>
      <p>
        {String(args.message ?? "Authenticate this connector to continue.")}
      </p>
      {typeof args.auth_url === "string" ? (
        <a className="aui-send-button" href={args.auth_url}>
          Connect
        </a>
      ) : null}
    </div>
  );
}

function AssistantActionBar(): ReactElement {
  return (
    <ActionBarPrimitive.Root
      className="aui-action-bar"
      hideWhenRunning
      autohide="not-last"
      autohideFloat="single-branch"
    >
      <ActionBarPrimitive.Copy className="aui-action">
        Copy
      </ActionBarPrimitive.Copy>
      <ActionBarPrimitive.Reload className="aui-action">
        Retry
      </ActionBarPrimitive.Reload>
      <ActionBarPrimitive.Speak className="aui-action">
        Speak
      </ActionBarPrimitive.Speak>
      <ActionBarPrimitive.StopSpeaking className="aui-action">
        Stop
      </ActionBarPrimitive.StopSpeaking>
    </ActionBarPrimitive.Root>
  );
}

function UserActionBar(): ReactElement {
  return (
    <ActionBarPrimitive.Root
      className="aui-action-bar"
      hideWhenRunning
      autohide="not-last"
      autohideFloat="single-branch"
    >
      <ActionBarPrimitive.Copy className="aui-action">
        Copy
      </ActionBarPrimitive.Copy>
      <ActionBarPrimitive.Edit className="aui-action">
        Edit
      </ActionBarPrimitive.Edit>
    </ActionBarPrimitive.Root>
  );
}

function MessageBranchPicker(): ReactElement {
  return (
    <BranchPickerPrimitive.Root
      className="aui-branch-picker"
      hideWhenSingleBranch
    >
      <BranchPickerPrimitive.Previous className="aui-action">
        ‹
      </BranchPickerPrimitive.Previous>
      <span>
        <BranchPickerPrimitive.Number /> / <BranchPickerPrimitive.Count />
      </span>
      <BranchPickerPrimitive.Next className="aui-action">
        ›
      </BranchPickerPrimitive.Next>
    </BranchPickerPrimitive.Root>
  );
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function formatToolValue(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function toolStatusLabel(status: string, isError?: boolean): string {
  if (isError) {
    return "error";
  }
  if (status === "requires-action") {
    return "waiting";
  }
  if (status === "running") {
    return "running";
  }
  return "complete";
}
