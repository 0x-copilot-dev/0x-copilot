import {
  ActionBarPrimitive,
  AuiIf,
  ComposerPrimitive,
  MessagePrimitive,
  SelectionToolbarPrimitive,
  SuggestionPrimitive,
  ThreadListItemPrimitive,
  ThreadListPrimitive,
  ThreadPrimitive,
  useAui,
  unstable_useMentionAdapter,
  unstable_useSlashCommandAdapter,
  type ReasoningGroupProps,
  type ReasoningMessagePartProps,
  type TextMessagePartProps,
  type ThreadMessageLike,
  type ToolCallMessagePartProps,
  type Unstable_MentionCategory,
  type Unstable_SlashCommand,
} from "@assistant-ui/react";
import { Streamdown } from "streamdown";
import type { ReactElement, ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import type {
  AssistantPerformanceMetrics,
  ApprovalDecision,
  Conversation,
  McpServer,
  ModelCatalogModel,
  Skill,
} from "@enterprise-search/api-types";
import { isAssistantPerformanceMetrics } from "@enterprise-search/api-types";

export function AssistantThreadList({
  collapsed,
  conversations,
  loading,
  activeRunId,
  onOpenSettings,
  onRefresh,
}: {
  collapsed: boolean;
  conversations: Conversation[];
  loading: boolean;
  activeRunId: string | null;
  onOpenSettings: () => void;
  onRefresh: () => void;
}): ReactElement {
  return (
    <aside
      className="aui-sidebar"
      data-collapsed={collapsed ? "true" : undefined}
      aria-label="Conversation history"
      aria-hidden={collapsed}
    >
      <div className="aui-sidebar__header">
        <LogoMark />
        <button
          className="aui-icon-button"
          type="button"
          aria-label="Refresh conversations"
          data-tooltip="Refresh conversations"
          onClick={onRefresh}
        >
          ↻
        </button>
      </div>
      <ThreadListPrimitive.Root className="aui-thread-list">
        <ThreadListPrimitive.New
          className="aui-new-thread"
          disabled={activeRunId !== null}
          title={
            activeRunId === null
              ? "Start a new thread"
              : "Stop the current response before starting a new thread"
          }
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
                title={
                  activeRunId === null
                    ? "Open thread"
                    : "Stop the current response before switching threads"
                }
              >
                <ThreadListItemPrimitive.Title />
              </ThreadListItemPrimitive.Trigger>
            </ThreadListItemPrimitive.Root>
          )}
        </ThreadListPrimitive.Items>
      </ThreadListPrimitive.Root>
      <div className="aui-sidebar__footer">
        <button
          className="aui-sidebar-settings"
          type="button"
          title="Open settings"
          onClick={onOpenSettings}
        >
          <span aria-hidden="true">⚙</span>
          Settings
        </button>
      </div>
    </aside>
  );
}

function ModelSelector({
  models,
  value,
  onChange,
  disabled,
}: {
  models: Array<ModelCatalogModel & { disabled?: boolean }>;
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
          <option key={model.id} value={model.id} disabled={model.disabled}>
            {model.name}
          </option>
        ))}
      </select>
      <span aria-hidden="true">⌄</span>
    </label>
  );
}

export function AssistantThread({
  sidebarCollapsed,
  status,
  models,
  selectedModel,
  onModelChange,
  modelDisabled,
  onShare,
  onToggleSidebar,
  children,
}: {
  sidebarCollapsed: boolean;
  status: string;
  models: Array<ModelCatalogModel & { disabled?: boolean }>;
  selectedModel: string;
  onModelChange: (modelId: string) => void;
  modelDisabled?: boolean;
  onShare: () => void;
  onToggleSidebar: () => void;
  children: ReactNode;
}): ReactElement {
  return (
    <section className="aui-chat-panel">
      <header className="aui-chat-header">
        <div className="aui-chat-header__left">
          <button
            className="aui-icon-button"
            type="button"
            aria-label={sidebarCollapsed ? "Show sidebar" : "Hide sidebar"}
            data-tooltip={sidebarCollapsed ? "Show sidebar" : "Hide sidebar"}
            onClick={onToggleSidebar}
          >
            {sidebarCollapsed ? "☰" : "◧"}
          </button>
          {sidebarCollapsed ? <LogoMark compact /> : null}
          <ModelSelector
            models={models}
            value={selectedModel}
            onChange={onModelChange}
            disabled={modelDisabled}
          />
        </div>
        <div className="aui-chat-header__actions">
          <span className="aui-status-pill">{status}</span>
          <button
            className="aui-ghost-button"
            type="button"
            title="Copy share link"
            onClick={onShare}
          >
            Share
          </button>
        </div>
      </header>
      <div className="not-prose aui-demo-frame">{children}</div>
    </section>
  );
}

function LogoMark({ compact = false }: { compact?: boolean }): ReactElement {
  return (
    <div className="aui-logo" aria-label="assistant-ui">
      <span className="aui-logo__mark" aria-hidden="true">
        ✦
      </span>
      {compact ? null : <span>assistant-ui</span>}
    </div>
  );
}

export function ThreadBody({
  oauthStatus,
  connectors,
  skills,
  connectorSuggestions,
  onMcpAuthConnect,
  onMcpAuthSkip,
  onOpenMcpSettings,
  onOpenSkillsSettings,
  onShowConnectors,
}: {
  oauthStatus: string | null;
  connectors: {
    servers: McpServer[];
    loading: boolean;
  };
  skills: {
    skills: Skill[];
    loading: boolean;
  };
  connectorSuggestions: ReactNode;
  onMcpAuthConnect: (serverId: string) => Promise<void>;
  onMcpAuthSkip: (serverId: string) => Promise<void>;
  onOpenMcpSettings: () => void;
  onOpenSkillsSettings: () => void;
  onShowConnectors: () => void;
}): ReactElement {
  return (
    <ThreadPrimitive.Root className="aui-thread-root">
      <SelectionToolbarPrimitive.Root className="aui-selection-toolbar">
        <SelectionToolbarPrimitive.Quote
          className="aui-selection-toolbar__button"
          title="Quote selected text"
        >
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
            return (
              <AssistantMessage
                message={message}
                onMcpAuthConnect={onMcpAuthConnect}
                onMcpAuthSkip={onMcpAuthSkip}
              />
            );
          }}
        </ThreadPrimitive.Messages>
        {connectorSuggestions}
        <ThreadPrimitive.ViewportFooter className="aui-thread-footer">
          <ThreadPrimitive.ScrollToBottom
            className="aui-scroll-bottom"
            title="Scroll to bottom"
          >
            Scroll to bottom
          </ThreadPrimitive.ScrollToBottom>
          <AssistantComposer
            connectors={connectors}
            skills={skills}
            onOpenMcpSettings={onOpenMcpSettings}
            onOpenSkillsSettings={onOpenSkillsSettings}
            onShowConnectors={onShowConnectors}
          />
        </ThreadPrimitive.ViewportFooter>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
}

function ThreadWelcome(): ReactElement {
  return (
    <section className="aui-welcome">
      <LogoMark compact />
      <h2>Hello there!</h2>
      <p>How can I help you today?</p>
      <div className="aui-suggestions">
        <ThreadPrimitive.Suggestions>
          {() => (
            <SuggestionPrimitive.Trigger
              className="aui-suggestion"
              title="Send this suggestion"
              send
            >
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

type ComposerMenuView = "root" | "mcp" | "skills";

function AssistantComposer({
  connectors,
  skills,
  onOpenMcpSettings,
  onOpenSkillsSettings,
  onShowConnectors,
}: {
  connectors: {
    servers: McpServer[];
    loading: boolean;
  };
  skills: {
    skills: Skill[];
    loading: boolean;
  };
  onOpenMcpSettings: () => void;
  onOpenSkillsSettings: () => void;
  onShowConnectors: () => void;
}): ReactElement {
  const aui = useAui();
  const menuRef = useRef<HTMLDivElement | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuView, setMenuView] = useState<ComposerMenuView>("root");
  const slash = unstable_useSlashCommandAdapter({
    commands: useMemo<readonly Unstable_SlashCommand[]>(
      () => [
        {
          id: "summarize",
          label: "Summarize",
          description: "Summarize the conversation.",
          execute: () => undefined,
        },
        {
          id: "translate",
          label: "Translate",
          description: "Translate text to another language.",
          execute: () => undefined,
        },
        {
          id: "search",
          label: "Search",
          description: "Search connected context.",
          execute: () => undefined,
        },
        {
          id: "help",
          label: "Help",
          description: "List available commands.",
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

  useEffect(() => {
    if (!menuOpen) {
      return;
    }
    function onPointerDown(event: PointerEvent): void {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
        setMenuView("root");
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [menuOpen]);

  function openFilePicker(accept: string): void {
    const input = document.createElement("input");
    input.type = "file";
    input.multiple = true;
    input.accept = accept;
    input.hidden = true;
    document.body.appendChild(input);
    input.onchange = () => {
      const files = input.files;
      if (files) {
        for (const file of files) {
          void aui.composer().addAttachment(file);
        }
      }
      input.remove();
      setMenuOpen(false);
      setMenuView("root");
    };
    input.oncancel = () => input.remove();
    input.click();
  }

  function appendComposerInstruction(text: string): void {
    const composer = aui.composer();
    const currentText = composer.getState().text.trimEnd();
    composer.setText(currentText ? `${currentText}\n${text}` : text);
    setMenuOpen(false);
    setMenuView("root");
  }

  return (
    <ComposerPrimitive.Unstable_TriggerPopoverRoot>
      <ComposerPrimitive.Root
        className="aui-composer"
        data-slot="aui_composer-shell"
      >
        <ComposerPrimitive.AttachmentDropzone className="aui-composer__dropzone">
          <ComposerPrimitive.Quote className="aui-quote-preview">
            <ComposerPrimitive.QuoteText />
            <ComposerPrimitive.QuoteDismiss
              className="aui-icon-button"
              aria-label="Remove quoted text"
              data-tooltip="Remove quoted text"
            >
              ×
            </ComposerPrimitive.QuoteDismiss>
          </ComposerPrimitive.Quote>
          <div className="aui-composer-attachments">
            <ComposerPrimitive.Attachments>
              {({ attachment }) => <AttachmentPill attachment={attachment} />}
            </ComposerPrimitive.Attachments>
          </div>
          <ComposerPrimitive.Input
            className="aui-composer__input"
            aria-label="Message"
            placeholder="Send a message... (@ to mention, / for commands)"
            minRows={1}
            maxRows={5}
            submitMode="enter"
          />
          <div className="aui-composer-action-wrapper">
            <div className="aui-plus-menu-root" ref={menuRef}>
              <button
                className="aui-icon-button aui-composer-add-attachment"
                type="button"
                aria-expanded={menuOpen}
                aria-haspopup="menu"
                aria-label="Open attachment and tools menu"
                data-tooltip="Add attachment"
                onClick={() => {
                  setMenuOpen((current) => !current);
                  setMenuView("root");
                }}
              >
                +
              </button>
              {menuOpen ? (
                <ComposerPlusMenu
                  view={menuView}
                  connectors={connectors}
                  skills={skills}
                  onBack={() => setMenuView("root")}
                  onAttachImage={() => openFilePicker("image/*")}
                  onAttachFile={() => openFilePicker(fileAttachmentAccept)}
                  onOpenMcp={() => setMenuView("mcp")}
                  onOpenSkills={() => setMenuView("skills")}
                  onOpenMcpSettings={onOpenMcpSettings}
                  onOpenSkillsSettings={onOpenSkillsSettings}
                  onShowConnectors={() => {
                    onShowConnectors();
                    setMenuOpen(false);
                    setMenuView("root");
                  }}
                  onUseMcpServer={(server) =>
                    appendComposerInstruction(
                      `Use the ${server.display_name} MCP server for this request.`,
                    )
                  }
                  onUseSkill={(skill) =>
                    appendComposerInstruction(
                      `Use the ${skill.display_name} skill for this request.`,
                    )
                  }
                />
              ) : null}
            </div>
            <AuiIf condition={(state) => !state.thread.isRunning}>
              <ComposerPrimitive.Send
                className="aui-send-button aui-composer-send"
                aria-label="Send message"
                data-tooltip="Send message"
              >
                ↑
              </ComposerPrimitive.Send>
            </AuiIf>
            <AuiIf condition={(state) => state.thread.isRunning}>
              <ComposerPrimitive.Cancel
                className="aui-send-button aui-send-button--stop"
                aria-label="Stop response"
                data-tooltip="Stop response"
              >
                Stop
              </ComposerPrimitive.Cancel>
            </AuiIf>
          </div>
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

function ComposerPlusMenu({
  view,
  connectors,
  skills,
  onBack,
  onAttachImage,
  onAttachFile,
  onOpenMcp,
  onOpenSkills,
  onOpenMcpSettings,
  onOpenSkillsSettings,
  onShowConnectors,
  onUseMcpServer,
  onUseSkill,
}: {
  view: ComposerMenuView;
  connectors: {
    servers: McpServer[];
    loading: boolean;
  };
  skills: {
    skills: Skill[];
    loading: boolean;
  };
  onBack: () => void;
  onAttachImage: () => void;
  onAttachFile: () => void;
  onOpenMcp: () => void;
  onOpenSkills: () => void;
  onOpenMcpSettings: () => void;
  onOpenSkillsSettings: () => void;
  onShowConnectors: () => void;
  onUseMcpServer: (server: McpServer) => void;
  onUseSkill: (skill: Skill) => void;
}): ReactElement {
  const enabledSkills = skills.skills.filter((skill) => skill.enabled);

  if (view === "mcp") {
    return (
      <div className="aui-plus-menu" role="menu" aria-label="MCP server menu">
        <button
          className="aui-trigger-popover__back"
          type="button"
          title="Back to attachment and tools menu"
          onClick={onBack}
        >
          Back
        </button>
        <div className="aui-plus-menu__section">
          <strong>MCP Servers</strong>
          {connectors.loading ? (
            <span>Loading servers...</span>
          ) : connectors.servers.length === 0 ? (
            <span>No MCP servers configured.</span>
          ) : (
            connectors.servers.map((server) => (
              <button
                key={server.server_id}
                className="aui-trigger-popover__item"
                type="button"
                role="menuitem"
                title={`Use ${server.display_name} MCP server`}
                onClick={() => onUseMcpServer(server)}
              >
                <strong>{server.display_name}</strong>
                <span>
                  {server.enabled ? "Enabled" : "Disabled"} -{" "}
                  {server.auth_state.replaceAll("_", " ")}
                </span>
              </button>
            ))
          )}
        </div>
        <button
          className="aui-trigger-popover__item"
          type="button"
          role="menuitem"
          title="Show connector suggestions"
          onClick={onShowConnectors}
        >
          <strong>Show connector suggestions</strong>
          <span>Review servers that need authentication.</span>
        </button>
        <button
          className="aui-trigger-popover__item"
          type="button"
          role="menuitem"
          title="Open MCP settings"
          onClick={onOpenMcpSettings}
        >
          <strong>Open MCP settings</strong>
          <span>Manage connector auth and server configuration.</span>
        </button>
      </div>
    );
  }

  if (view === "skills") {
    return (
      <div className="aui-plus-menu" role="menu" aria-label="Skills menu">
        <button
          className="aui-trigger-popover__back"
          type="button"
          title="Back to attachment and tools menu"
          onClick={onBack}
        >
          Back
        </button>
        <div className="aui-plus-menu__section">
          <strong>Skills</strong>
          {skills.loading ? (
            <span>Loading skills...</span>
          ) : enabledSkills.length === 0 ? (
            <span>No enabled skills yet.</span>
          ) : (
            enabledSkills.map((skill) => (
              <button
                key={skill.skill_id}
                className="aui-trigger-popover__item"
                type="button"
                role="menuitem"
                title={`Use ${skill.display_name} skill`}
                onClick={() => onUseSkill(skill)}
              >
                <strong>{skill.display_name}</strong>
                <span>{skill.description || skill.name}</span>
              </button>
            ))
          )}
        </div>
        <button
          className="aui-trigger-popover__item"
          type="button"
          role="menuitem"
          title="Open skill settings"
          onClick={onOpenSkillsSettings}
        >
          <strong>Open skill settings</strong>
          <span>Manage available skills.</span>
        </button>
      </div>
    );
  }

  return (
    <div
      className="aui-plus-menu"
      role="menu"
      aria-label="Attachment and tools menu"
    >
      <button
        className="aui-trigger-popover__item"
        type="button"
        role="menuitem"
        title="Attach an image"
        onClick={onAttachImage}
      >
        <strong>Attach Image</strong>
        <span>Upload PNG, JPG, GIF, or WebP images.</span>
      </button>
      <button
        className="aui-trigger-popover__item"
        type="button"
        role="menuitem"
        title="Attach a file"
        onClick={onAttachFile}
      >
        <strong>Attach File</strong>
        <span>Upload PDF, DOCX, spreadsheets, slides, or text files.</span>
      </button>
      <button
        className="aui-trigger-popover__item"
        type="button"
        role="menuitem"
        title="Open MCP server menu"
        onClick={onOpenMcp}
      >
        <strong>MCP Servers</strong>
        <span>Choose an available server or open MCP settings.</span>
      </button>
      <button
        className="aui-trigger-popover__item"
        type="button"
        role="menuitem"
        title="Open skills menu"
        onClick={onOpenSkills}
      >
        <strong>Skills</strong>
        <span>Choose an enabled skill or open skill settings.</span>
      </button>
    </div>
  );
}

const fileAttachmentAccept =
  "text/plain,text/html,text/markdown,text/csv,text/xml,text/json,text/css,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-powerpoint,application/vnd.openxmlformats-officedocument.presentationml.presentation,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx";

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
              title={`Open ${category.label}`}
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
              title={item.description ?? item.label}
            >
              <strong>{item.label}</strong>
              {item.description ? <span>{item.description}</span> : null}
            </ComposerPrimitive.Unstable_TriggerPopoverItem>
          ))
        }
      </ComposerPrimitive.Unstable_TriggerPopoverItems>
      <ComposerPrimitive.Unstable_TriggerPopoverBack
        className="aui-trigger-popover__back"
        title="Back"
      >
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

function AssistantMessage({
  message,
  onMcpAuthConnect,
  onMcpAuthSkip,
}: {
  message: {
    metadata?: ThreadMessageLike["metadata"];
    status?: ThreadMessageLike["status"];
  };
  onMcpAuthConnect: (serverId: string) => Promise<void>;
  onMcpAuthSkip: (serverId: string) => Promise<void>;
}): ReactElement {
  const metrics = performanceMetricsFromMetadata(message.metadata);
  const showFooter = isTerminalAssistantStatus(message.status);
  return (
    <MessagePrimitive.Root className="aui-message aui-message--assistant">
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
                run_progress: ProgressTool,
                approval_request: ApprovalTool,
                mcp_auth_required: (props) => (
                  <ConnectorAuthTool
                    {...props}
                    onConnect={onMcpAuthConnect}
                    onSkip={onMcpAuthSkip}
                  />
                ),
              },
            },
          }}
        />
      </div>
      {showFooter ? <AssistantMessageFooter metrics={metrics} /> : null}
    </MessagePrimitive.Root>
  );
}

function AssistantMessageFooter({
  metrics,
}: {
  metrics: AssistantPerformanceMetrics | null;
}): ReactElement {
  return (
    <div className="aui-assistant-message-footer">
      <ActionBarPrimitive.Root className="aui-assistant-action-bar">
        <ActionBarPrimitive.Copy
          className="aui-footer-icon-button"
          aria-label="Copy response"
          data-tooltip="Copy response"
        >
          <CopyIcon />
        </ActionBarPrimitive.Copy>
        <ActionBarPrimitive.Reload
          className="aui-footer-icon-button"
          aria-label="Retry response"
          data-tooltip="Retry response"
        >
          <RetryIcon />
        </ActionBarPrimitive.Reload>
      </ActionBarPrimitive.Root>
      {metrics ? <AssistantMessageMetrics metrics={metrics} /> : null}
    </div>
  );
}

function CopyIcon(): ReactElement {
  return (
    <svg
      aria-hidden="true"
      className="aui-footer-icon-button__icon"
      fill="none"
      focusable="false"
      viewBox="0 0 24 24"
    >
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

function RetryIcon(): ReactElement {
  return (
    <svg
      aria-hidden="true"
      className="aui-footer-icon-button__icon"
      fill="none"
      focusable="false"
      viewBox="0 0 24 24"
    >
      <path d="M20 12a8 8 0 1 1-2.34-5.66" />
      <path d="M20 4v6h-6" />
    </svg>
  );
}

function AssistantMessageMetrics({
  metrics,
}: {
  metrics: AssistantPerformanceMetrics;
}): ReactElement {
  const rows = metricRows(metrics);
  return (
    <div
      className="aui-message-metrics"
      aria-label={rows.map((row) => `${row.label}: ${row.value}`).join(", ")}
    >
      <span className="aui-message-timing" tabIndex={0}>
        {formatMilliseconds(metrics.duration_ms)}
      </span>
      <div className="aui-message-metrics__tooltip" role="tooltip">
        {rows.map((row) => (
          <div className="aui-message-metrics__row" key={row.label}>
            <span>{row.label}</span>
            <strong>{row.value}</strong>
          </div>
        ))}
      </div>
    </div>
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
          <ComposerPrimitive.Cancel
            className="aui-ghost-button"
            title="Cancel editing"
          >
            Cancel
          </ComposerPrimitive.Cancel>
          <ComposerPrimitive.Send
            className="aui-send-button"
            title="Save edited message"
          >
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

function MarkdownText({ text }: TextMessagePartProps): ReactElement {
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
  return <>{children}</>;
}

function ToolFallback({
  toolName,
  argsText,
  result,
  status,
  isError,
}: ToolCallMessagePartProps): ReactElement {
  const argsSummary = summarizeArgsText(argsText);
  const statusLabel = toolStatusLabel(status.type, isError);
  return (
    <details
      className="aui-tool-card aui-tool-card--activity"
      data-status={status.type}
    >
      <summary className="aui-tool-card__activity-summary">
        <strong>
          {toolActivitySummary(toolName, status.type, isError, result)}
        </strong>
        <span>{statusLabel}</span>
      </summary>
      <div className="aui-tool-card__activity-content">
        {argsSummary ? (
          <p className="aui-tool-card__summary">Input: {argsSummary}</p>
        ) : null}
        {result !== undefined ? (
          <p className="aui-tool-card__result">
            Output: {summarizeToolValue(result)}
          </p>
        ) : null}
        <ToolInputDetails argsText={argsText} />
        {result !== undefined ? (
          <>
            <small>Output details</small>
            <pre>{formatToolValue(result)}</pre>
          </>
        ) : null}
      </div>
    </details>
  );
}

function ToolInputDetails({
  argsText,
}: {
  argsText?: string;
}): ReactElement | null {
  if (!argsText) {
    return null;
  }
  const args = parseToolArgs(argsText);
  if (args === null) {
    return (
      <>
        <small>Input details</small>
        <pre>{argsText}</pre>
      </>
    );
  }

  const entries = visibleToolArgEntries(args);
  if (entries.length === 0) {
    return null;
  }

  return (
    <>
      <small>Input details</small>
      <dl className="aui-tool-card__fields">
        {entries.map(([key, value]) => (
          <div key={key} className="aui-tool-card__field">
            <dt>{formatArgLabel(key)}</dt>
            <dd>{formatDetailValue(value)}</dd>
          </div>
        ))}
      </dl>
    </>
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
        <p className="aui-tool-card__result">
          {summarizeToolValue(props.result)}
        </p>
      ) : null}
      <ToolDetails argsText={props.argsText} result={props.result} />
    </div>
  );
}

function ProgressTool(props: ToolCallMessagePartProps): ReactElement {
  const data = asRecord(props.args);
  const status =
    typeof data.status === "string"
      ? data.status
      : toolStatusLabel(props.status.type, props.isError);
  return (
    <div className="aui-tool-card" data-status={props.status.type}>
      <div className="aui-tool-card__header">
        <strong>{String(data.title ?? "Progress")}</strong>
        <span>{status}</span>
      </div>
      {typeof data.summary === "string" ? <p>{data.summary}</p> : null}
      <ToolDetails argsText={props.argsText} result={props.result} />
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
          <button
            type="button"
            title="Approve this request"
            onClick={() => submit("approved")}
          >
            Approve
          </button>
          <button
            type="button"
            title="Reject this request"
            onClick={() => submit("rejected")}
          >
            Reject
          </button>
        </div>
      ) : null}
    </div>
  );
}

function ConnectorAuthTool({
  args,
  result,
  status,
  onConnect,
  onSkip,
}: ToolCallMessagePartProps<Record<string, unknown>> & {
  onConnect: (serverId: string) => Promise<void>;
  onSkip: (serverId: string) => Promise<void>;
}): ReactElement {
  const [pendingAction, setPendingAction] = useState<"connect" | "skip" | null>(
    null,
  );
  const serverId = stringValue(args.server_id);
  const displayName =
    stringValue(args.display_name) ??
    stringValue(args.server_name) ??
    "connector";
  const message =
    stringValue(args.message) ?? "Authenticate this connector to continue.";
  const expiresAt = stringValue(args.expires_at);
  const resolved = result !== undefined || status.type === "complete";

  async function submit(action: "connect" | "skip"): Promise<void> {
    if (!serverId || resolved || pendingAction !== null) {
      return;
    }
    setPendingAction(action);
    try {
      if (action === "connect") {
        await onConnect(serverId);
      } else {
        await onSkip(serverId);
      }
    } finally {
      setPendingAction(null);
    }
  }

  return (
    <div className="aui-tool-card aui-tool-card--connector">
      <div className="aui-tool-card__header">
        <strong>Connect {displayName}</strong>
        <span>{resolved ? "resolved" : "action required"}</span>
      </div>
      <p>{message}</p>
      {expiresAt ? (
        <small>Link expires at {formatDateTime(expiresAt)}.</small>
      ) : null}
      {!resolved ? (
        <div className="aui-tool-card__actions">
          <button
            type="button"
            disabled={!serverId || pendingAction !== null}
            title={`Connect ${displayName}`}
            onClick={() => void submit("connect")}
          >
            {pendingAction === "connect" ? "Connecting..." : "Connect"}
          </button>
          <button
            type="button"
            disabled={!serverId || pendingAction !== null}
            title={`Skip ${displayName} authentication`}
            onClick={() => void submit("skip")}
          >
            {pendingAction === "skip" ? "Skipping..." : "Not now"}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function performanceMetricsFromMetadata(
  metadata: ThreadMessageLike["metadata"] | undefined,
): AssistantPerformanceMetrics | null {
  const metrics = metadata?.custom?.performance_metrics;
  return isAssistantPerformanceMetrics(metrics) ? metrics : null;
}

function isTerminalAssistantStatus(
  status: ThreadMessageLike["status"] | undefined,
): boolean {
  return status?.type === "complete" || status?.type === "incomplete";
}

function formatMilliseconds(value: number): string {
  if (value < 1000) {
    return `${Math.max(0, Math.round(value))}ms`;
  }
  return `${formatNumber(value / 1000)}s`;
}

function formatNumber(value: number): string {
  return Number.isInteger(value)
    ? String(value)
    : value.toFixed(2).replace(/\.?0+$/, "");
}

function metricRows(
  metrics: AssistantPerformanceMetrics,
): Array<{ label: string; value: string }> {
  const rows: Array<{ label: string; value: string }> = [];
  if (metrics.first_chunk_ms !== undefined) {
    rows.push({
      label: "First token",
      value: formatMilliseconds(metrics.first_chunk_ms),
    });
  }
  rows.push({
    label: "Total",
    value: formatMilliseconds(metrics.duration_ms),
  });
  if (metrics.usage?.output_per_second !== undefined) {
    rows.push({
      label: "Speed",
      value: `${formatNumber(metrics.usage.output_per_second)} tok/s`,
    });
  }
  return rows;
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function ToolDetails({
  argsText,
  result,
}: {
  argsText?: string;
  result?: unknown;
}): ReactElement | null {
  if (!argsText && result === undefined) {
    return null;
  }
  return (
    <details className="aui-tool-card__details">
      <summary>Details</summary>
      {argsText ? <pre>{argsText}</pre> : null}
      {result !== undefined ? <pre>{formatToolValue(result)}</pre> : null}
    </details>
  );
}

function summarizeArgsText(argsText?: string): string | null {
  if (!argsText) {
    return null;
  }
  return summarizeArgs(parseToolArgs(argsText));
}

function summarizeArgs(value: unknown): string | null {
  const entries = visibleToolArgEntries(asRecord(value));
  if (entries.length === 0) {
    return null;
  }
  return entries
    .slice(0, 3)
    .map(
      ([key, entry]) => `${formatArgLabel(key)}: ${formatInlineValue(entry)}`,
    )
    .join(" · ");
}

function summarizeToolValue(value: unknown): string {
  if (Array.isArray(value)) {
    return value.length === 0 ? "No results" : `${value.length} results`;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (trimmed === "[]") {
      return "No results";
    }
    return trimmed || "Completed";
  }
  const record = asRecord(value);
  const message =
    stringValue(record.message) ??
    stringValue(record.content) ??
    stringValue(record.summary);
  if (message) {
    return message;
  }
  const keys = Object.keys(record);
  return keys.length > 0 ? `${keys.length} fields returned` : "Completed";
}

function parseToolArgs(argsText: string): Record<string, unknown> | null {
  try {
    return asRecord(JSON.parse(argsText) as unknown);
  } catch {
    return null;
  }
}

function visibleToolArgEntries(
  args: Record<string, unknown>,
): Array<[string, unknown]> {
  return Object.entries(args).filter(([key, entry]) => {
    return !hiddenToolArgKeys.has(key) && entry !== null && entry !== undefined;
  });
}

const hiddenToolArgKeys = new Set([
  "status",
  "summary",
  "delta",
  "deltas",
  "event_type",
]);

function formatArgLabel(key: string): string {
  return key.replaceAll("_", " ");
}

function formatInlineValue(value: unknown): string {
  if (Array.isArray(value)) {
    return value.length === 0 ? "[]" : `${value.length} items`;
  }
  if (typeof value === "string") {
    return summarizeInlineString(value);
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (value && typeof value === "object") {
    return "{...}";
  }
  return String(value);
}

function summarizeInlineString(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) {
    return "empty";
  }
  const lineCount = trimmed.split(/\r\n|\r|\n/).length;
  if (lineCount > 1) {
    return `${lineCount} lines`;
  }
  return trimmed.length > 90 ? `${trimmed.slice(0, 87)}...` : trimmed;
}

function formatDetailValue(value: unknown): ReactNode {
  if (typeof value === "string") {
    return shouldRenderBlockValue(value) ? (
      <pre>{value}</pre>
    ) : (
      <span>{value}</span>
    );
  }
  if (
    typeof value === "number" ||
    typeof value === "boolean" ||
    value === null
  ) {
    return <span>{String(value)}</span>;
  }
  return <pre>{formatToolValue(value)}</pre>;
}

function shouldRenderBlockValue(value: string): boolean {
  return value.includes("\n") || value.length > 120;
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

function toolActivitySummary(
  toolName: string,
  status: string,
  isError: boolean | undefined,
  result: unknown,
): string {
  const displayName = toolDisplayName(toolName);
  if (isError || status === "incomplete") {
    return `Could not run ${displayName}`;
  }
  if (status === "running") {
    return `Calling ${displayName}`;
  }
  if (status === "requires-action") {
    return `${displayName} needs attention`;
  }
  if (result !== undefined) {
    const resultSummary = summarizeToolValue(result);
    return resultSummary === "Completed"
      ? `Called ${displayName}`
      : `Called ${displayName}: ${resultSummary}`;
  }
  return `Calling ${displayName}`;
}

function toolDisplayName(toolName: string): string {
  const trimmed = toolName.trim() || "tool";
  return /\btool$/i.test(trimmed) ? trimmed : `${trimmed} tool`;
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
