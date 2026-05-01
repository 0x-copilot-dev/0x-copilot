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
import {
  Badge,
  Button,
  Card,
  classNames,
} from "@enterprise-search/design-system";
import type {
  AssistantPerformanceMetrics,
  ApprovalDecision,
  Conversation,
  McpServer,
  ModelCatalogModel,
  Skill,
} from "@enterprise-search/api-types";
import { isAssistantPerformanceMetrics } from "@enterprise-search/api-types";
import { mcpServerInstructionPrompt, skillInstructionPrompt } from "./prompts";

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
          data-tooltip-placement="bottom"
          data-tooltip-align="end"
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
            data-tooltip-placement="bottom"
            data-tooltip-align="start"
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
  onMcpAuthConnect: (payload: {
    approvalId: string;
    serverId: string;
  }) => Promise<void>;
  onMcpAuthSkip: (payload: {
    approvalId: string;
    serverId: string;
  }) => Promise<void>;
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

type ActivityVariant =
  | "tool"
  | "mcp"
  | "subagent"
  | "approval"
  | "connector"
  | "progress";

type ActivityParam = {
  label: string;
  value: ReactNode;
  block?: boolean;
};

function ActivityCard({
  title,
  status,
  variant = "tool",
  description,
  params = [],
  result,
  details,
  children,
  className,
}: {
  title: string;
  status: string;
  variant?: ActivityVariant;
  description?: ReactNode;
  params?: ActivityParam[];
  result?: ReactNode;
  details?: ReactNode;
  children?: ReactNode;
  className?: string;
}): ReactElement {
  return (
    <Card
      className={classNames(
        "aui-tool-card",
        "aui-activity-card",
        `aui-activity-card--${variant}`,
        className,
      )}
      data-status={status}
    >
      <header className="aui-activity-card__header">
        <span className="aui-activity-card__status-dot" aria-hidden="true" />
        <div className="aui-activity-card__heading">
          <span className="aui-activity-card__title">{title}</span>
          {description ? (
            <p className="aui-activity-card__description">{description}</p>
          ) : null}
        </div>
        <Badge tone={badgeToneForStatus(status)}>{status}</Badge>
      </header>
      {params.length > 0 ? <ActivityParams params={params} /> : null}
      {result ? (
        <div className="aui-activity-card__result">{result}</div>
      ) : null}
      {children}
      {details ? <ActivityDetails>{details}</ActivityDetails> : null}
    </Card>
  );
}

function ActivityParams({ params }: { params: ActivityParam[] }): ReactElement {
  return (
    <dl className="aui-activity-card__params">
      {params.map((param) => (
        <div
          className={classNames(
            "aui-activity-card__param",
            param.block ? "aui-activity-card__param--block" : undefined,
          )}
          key={param.label}
        >
          <dt>{param.label}</dt>
          <dd>{param.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function ActivityDetails({ children }: { children: ReactNode }): ReactElement {
  return (
    <ActivityCollapsible
      className="aui-activity-card__details"
      contentClassName="aui-activity-card__details-content"
      label="Inspect details"
    >
      {children}
    </ActivityCollapsible>
  );
}

function ActivityCollapsible({
  label,
  children,
  className,
  contentClassName,
}: {
  label: string;
  children: ReactNode;
  className?: string;
  contentClassName?: string;
}): ReactElement {
  return (
    <details className={classNames("aui-collapsible", className)}>
      <summary className="aui-collapsible__trigger">{label}</summary>
      <div className={classNames("aui-collapsible__content", contentClassName)}>
        {children}
      </div>
    </details>
  );
}

function ActivityItem({
  title,
  status,
  variant = "tool",
  description,
  details,
  result,
  icon,
}: {
  title: string;
  status: string;
  variant?: ActivityVariant;
  description?: ReactNode;
  details?: ReactNode;
  result?: ReactNode;
  icon?: ReactNode;
}): ReactElement {
  const hasDetails = Boolean(details);
  return (
    <div
      className={classNames(
        "aui-activity-item",
        `aui-activity-item--${variant}`,
      )}
      data-status={status}
    >
      <div className="aui-activity-item__content">
        <span className="aui-activity-item__icon" aria-hidden="true">
          {icon ?? <ActivityStatusIcon status={status} />}
        </span>
        <div className="aui-activity-item__text">
          <div className="aui-activity-item__line">
            <span className="aui-activity-item__title">{title}</span>
            {description ? (
              <span className="aui-activity-item__description">
                {description}
              </span>
            ) : null}
          </div>
          {result ? (
            <div className="aui-activity-item__result">{result}</div>
          ) : null}
        </div>
      </div>
      <span className="aui-activity-item__status">{status}</span>
      {hasDetails ? (
        <ActivityCollapsible
          className="aui-activity-item__details"
          contentClassName="aui-activity-item__details-content"
          label="Details"
        >
          {details}
        </ActivityCollapsible>
      ) : null}
    </div>
  );
}

function ActivityStatusIcon({ status }: { status: string }): ReactElement {
  const normalized = status.toLowerCase();
  if (
    normalized === "running" ||
    normalized === "starting" ||
    normalized === "working" ||
    normalized === "still working" ||
    normalized === "waiting"
  ) {
    return <span className="aui-activity-item__spinner" />;
  }
  if (
    normalized === "error" ||
    normalized === "failed" ||
    normalized === "could not complete"
  ) {
    return <span className="aui-activity-item__mark">!</span>;
  }
  return <span className="aui-activity-item__mark">✓</span>;
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
                      mcpServerInstructionPrompt(server.display_name),
                    )
                  }
                  onUseSkill={(skill) =>
                    appendComposerInstruction(
                      skillInstructionPrompt(skill.display_name),
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
  onMcpAuthConnect: (payload: {
    approvalId: string;
    serverId: string;
  }) => Promise<void>;
  onMcpAuthSkip: (payload: {
    approvalId: string;
    serverId: string;
  }) => Promise<void>;
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
                auth_mcp: McpTool,
                call_mcp_tool: McpTool,
                load_mcp_server: McpTool,
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
      <summary>
        <ThinkingIcon />
        <span>Thinking</span>
      </summary>
      <div className="aui-reasoning-group__content">{children}</div>
    </details>
  );
}

function ThinkingIcon(): ReactElement {
  return (
    <svg
      aria-hidden="true"
      className="aui-reasoning-group__icon"
      fill="none"
      focusable="false"
      viewBox="0 0 24 24"
    >
      <circle cx="12" cy="12" r="9" strokeWidth="1.5" stroke="currentColor" />
      <path
        d="M9 12h6M12 9v6"
        strokeWidth="1.5"
        stroke="currentColor"
        strokeLinecap="round"
      />
    </svg>
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
  args,
  argsText,
  result,
  status,
  isError,
}: ToolCallMessagePartProps<Record<string, unknown>>): ReactElement {
  const argsSummary = summarizeArgsText(argsText);
  const activitySummary = stringValue(args.summary) ?? argsSummary;
  const statusLabel = toolStatusLabel(status.type, isError);
  const largeResult = largeToolResultFromValue(result);
  const title = inlineToolTitle(toolName, status.type, isError);
  const resultSummary = largeResult
    ? "large result saved"
    : result !== undefined
      ? summarizeToolValue(result, toolName)
      : undefined;
  const details = toolDetailsContent(argsText, result);
  if (!shouldRenderFullToolCard(status.type, isError, result)) {
    return (
      <ActivityItem
        title={title}
        status={statusLabel}
        variant="tool"
        description={activitySummary}
        result={resultSummary}
        details={details}
      />
    );
  }
  return (
    <ActivityCard
      title={title}
      status={statusLabel}
      variant="tool"
      description={activitySummary}
      params={activityParams(argsText, args)}
      result={resultSummary}
      details={details}
    />
  );
}

function McpTool({
  toolName,
  args,
  argsText,
  result,
  status,
  isError,
}: ToolCallMessagePartProps<Record<string, unknown>>): ReactElement {
  const serverName = stringValue(args.server_name);
  const requestedTool = stringValue(args.tool_name);
  const resultNotice = largeToolResultFromValue(result);
  const statusLabel = toolStatusLabel(status.type, isError);
  const title = inlineMcpToolTitle(toolName, requestedTool);
  const description = mcpToolSummary(
    toolName,
    status.type,
    serverName,
    requestedTool,
  );
  const resultSummary = resultNotice
    ? "large result saved"
    : result !== undefined
      ? summarizeMcpResult(result)
      : undefined;
  const details = toolDetailsContent(argsText, result);
  if (!shouldRenderFullMcpCard(toolName, status.type, isError, result)) {
    return (
      <ActivityItem
        title={title}
        status={statusLabel}
        variant="mcp"
        description={description}
        result={resultSummary}
        details={details}
      />
    );
  }
  return (
    <ActivityCard
      title={title}
      status={statusLabel}
      variant="mcp"
      description={description}
      params={mcpActivityParams(serverName, requestedTool, args.arguments)}
      result={resultSummary}
      details={details}
    />
  );
}

function SubagentTool(props: ToolCallMessagePartProps): ReactElement {
  const data = asRecord(props.args);
  const subagentName =
    stringValue(data.subagent_name) ?? stringValue(data.name);
  const taskId = stringValue(data.task_id);
  const summary = stringValue(data.summary);
  const shortSummary = stringValue(data.short_summary);
  const taskSummary = shortSummary ?? stringValue(data.task_summary) ?? summary;
  const displayTitle = stringValue(data.display_title);
  const activities = subagentActivityRecords(data.activities);
  const dataStatus = stringValue(data.status);
  const normalizedStatus = dataStatus?.toLowerCase();
  const completed =
    props.status.type === "complete" ||
    ["completed", "succeeded", "success"].includes(normalizedStatus ?? "");
  const failed =
    props.isError === true ||
    normalizedStatus === "failed" ||
    normalizedStatus === "error";
  const cancelled = normalizedStatus === "cancelled";
  const terminal = completed || failed || cancelled;
  const elapsedSeconds = useElapsedSeconds(
    !terminal,
    stringValue(data.started_at),
  );
  const statusLabel = subagentStatusLabel(
    dataStatus ?? props.status.type,
    props.isError,
    elapsedSeconds,
  );
  const title = subagentCardTitle(displayTitle, taskSummary, completed);
  const fallbackProgress = subagentFallbackProgress(elapsedSeconds);
  const outputSummary = terminal
    ? summarizeSubagentResult(summary, taskSummary)
    : fallbackProgress;
  const details =
    import.meta.env.DEV && (taskId || subagentName) ? (
      <>
        {subagentName ? (
          <small>Agent: {formatAgentName(subagentName)}</small>
        ) : null}
        {taskId ? <small>Task ID: {taskId}</small> : null}
      </>
    ) : undefined;
  const hasActivityDetail = activities.length > 0;
  const activityDetails = hasActivityDetail ? (
    <SubagentActivityList
      activities={activities}
      emptyText={
        completed ? "No detailed activity was reported." : fallbackProgress
      }
    />
  ) : null;
  const resultDetails =
    terminal && summary ? (
      <>
        <small>Result</small>
        <pre>{truncateText(summary, 800)}</pre>
      </>
    ) : null;
  const subagentDetails =
    activityDetails || details || resultDetails ? (
      <>
        {activityDetails}
        {details}
        {resultDetails}
      </>
    ) : undefined;
  return (
    <ActivityItem
      title={subagentInlineTitle(completed, failed, cancelled)}
      status={statusLabel}
      variant="subagent"
      description={title}
      result={terminal ? undefined : outputSummary}
      details={subagentDetails}
    />
  );
}

function useElapsedSeconds(active: boolean, startedAt: string | null): number {
  const [mountedAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) {
      return undefined;
    }
    const timer = window.setInterval(() => setNow(Date.now()), 5000);
    return () => window.clearInterval(timer);
  }, [active]);
  const parsedStartedAt = startedAt ? Date.parse(startedAt) : Number.NaN;
  const startMs = Number.isFinite(parsedStartedAt)
    ? parsedStartedAt
    : mountedAt;
  return Math.max(0, Math.floor((now - startMs) / 1000));
}

function subagentCardTitle(
  displayTitle: string | null,
  taskSummary: string | null,
  completed: boolean,
): string {
  const title = displayTitle ?? taskSummary;
  if (title) {
    return truncateText(title, 96);
  }
  return completed ? "Background task finished" : "Working in the background";
}

function subagentInlineTitle(
  completed: boolean,
  failed: boolean,
  cancelled: boolean,
): string {
  if (failed) {
    return "Subagent failed";
  }
  if (cancelled) {
    return "Subagent cancelled";
  }
  return completed ? "Subagent finished" : "Subagent working";
}

function subagentStatusLabel(
  status: string,
  isError: boolean | undefined,
  elapsedSeconds: number,
): string {
  const normalized = status.toLowerCase();
  if (isError || normalized === "failed" || normalized === "error") {
    return "could not complete";
  }
  if (normalized === "cancelled") {
    return "cancelled";
  }
  if (
    normalized === "complete" ||
    normalized === "completed" ||
    normalized === "succeeded" ||
    normalized === "success"
  ) {
    return "done";
  }
  if (elapsedSeconds >= 35) {
    return "still working";
  }
  if (normalized === "queued" || normalized === "started") {
    return "starting";
  }
  return "working";
}

function subagentFallbackProgress(elapsedSeconds: number): string {
  if (elapsedSeconds >= 35) {
    return "Still working. Larger tasks can take about a minute.";
  }
  if (elapsedSeconds >= 15) {
    return "Working through the details...";
  }
  if (elapsedSeconds >= 5) {
    return "Gathering context...";
  }
  return "Starting task...";
}

function summarizeSubagentResult(
  summary: string | null,
  taskSummary: string | null,
): string | undefined {
  if (!summary || summary === taskSummary) {
    return undefined;
  }
  return truncateText(summary, 140);
}

function SubagentActivityList({
  activities,
  emptyText = "No detailed activity was reported.",
}: {
  activities: SubagentActivityRecord[];
  emptyText?: string;
}): ReactElement {
  if (activities.length === 0) {
    return <p className="aui-tool-card__empty">{emptyText}</p>;
  }
  return (
    <div className="aui-tool-card__timeline">
      {activities.map((activity) => (
        <div className="aui-tool-card__timeline-item" key={activity.id}>
          <div>
            <span className="aui-tool-card__timeline-title">
              {activityTitle(activity)}
            </span>
            {activity.summary ? (
              <p>{truncateText(activity.summary, 160)}</p>
            ) : null}
            {!activity.summary && activity.inputSummary ? (
              <p>{truncateText(activity.inputSummary, 160)}</p>
            ) : null}
            {activity.result ? (
              <p>{truncateText(activity.result, 160)}</p>
            ) : null}
          </div>
          <span>{activity.status}</span>
        </div>
      ))}
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
    <ActivityCard
      title={String(data.title ?? "Progress")}
      status={status}
      variant="progress"
      description={typeof data.summary === "string" ? data.summary : undefined}
      details={toolDetailsContent(props.argsText, props.result)}
    />
  );
}

function ApprovalTool({
  args,
  result,
  resume,
}: ToolCallMessagePartProps<Record<string, unknown>>): ReactElement {
  const approvalId = String(args.approval_id ?? "");
  const toolName = stringValue(args.tool_name);
  const serverName = stringValue(args.server_name);
  const displayName = stringValue(args.display_name) ?? serverName;
  const riskLevel = stringValue(args.risk_level);
  const readOnly = typeof args.read_only === "boolean" ? args.read_only : null;
  const isMcpApproval =
    stringValue(args.approval_kind) === "mcp_tool" ||
    stringValue(args.kind) === "mcp_tool";
  const resolved = result !== undefined;
  const submit = (decision: ApprovalDecision): void => {
    resume({ decision, approval_id: approvalId });
  };
  const approvalStatus = resolved ? "resolved" : "waiting";
  return (
    <ActivityCard
      title={
        isMcpApproval ? "Connector action needs approval" : "Approval requested"
      }
      status={approvalStatus}
      variant="approval"
      description={String(args.message ?? args.reason ?? approvalId)}
      params={
        isMcpApproval
          ? [
              ...mcpActivityParams(displayName, toolName, args.arguments),
              ...(riskLevel
                ? [{ label: "Risk", value: <Badge>{riskLevel}</Badge> }]
                : []),
              ...(readOnly !== null
                ? [
                    {
                      label: "Access",
                      value: readOnly ? "Read-only" : "May change data",
                    },
                  ]
                : []),
            ]
          : []
      }
      details={approvalDetailsContent(args, result)}
    >
      {!resolved ? (
        <div className="aui-tool-card__actions">
          <Button
            type="button"
            size="sm"
            title={
              isMcpApproval ? "Execute this MCP tool" : "Approve this request"
            }
            onClick={() => submit("approved")}
          >
            {isMcpApproval ? "Execute" : "Approve"}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            title={
              isMcpApproval ? "Decline this MCP tool" : "Reject this request"
            }
            onClick={() => submit("rejected")}
          >
            {isMcpApproval ? "Decline" : "Reject"}
          </Button>
        </div>
      ) : null}
    </ActivityCard>
  );
}

function ConnectorAuthTool({
  args,
  result,
  onConnect,
  onSkip,
  resume,
}: ToolCallMessagePartProps<Record<string, unknown>> & {
  onConnect: (payload: {
    approvalId: string;
    serverId: string;
  }) => Promise<void>;
  onSkip: (payload: { approvalId: string; serverId: string }) => Promise<void>;
}): ReactElement {
  const [pendingAction, setPendingAction] = useState<"connect" | "skip" | null>(
    null,
  );
  const serverId = stringValue(args.server_id);
  const approvalId =
    stringValue(args.approval_id) ?? stringValue(args.action_id) ?? serverId;
  const displayName =
    stringValue(args.display_name) ??
    stringValue(args.server_name) ??
    "connector";
  const message =
    stringValue(args.message) ?? "Authenticate this connector to continue.";
  const expiresAt = stringValue(args.expires_at);
  const resolved = result !== undefined;

  async function submit(action: "connect" | "skip"): Promise<void> {
    if (!serverId || !approvalId || resolved || pendingAction !== null) {
      return;
    }
    setPendingAction(action);
    try {
      if (action === "connect") {
        await onConnect({ approvalId, serverId });
      } else {
        await onSkip({ approvalId, serverId });
        const result = {
          approval_id: approvalId,
          approval_kind: "mcp_auth",
          decision: "rejected",
          server_id: serverId,
        };
        resume(result);
      }
    } finally {
      setPendingAction(null);
    }
  }

  return (
    <ActivityCard
      title={`Connect ${displayName}`}
      status={resolved ? "resolved" : "action required"}
      variant="connector"
      description={message}
      params={
        expiresAt
          ? [{ label: "Link expires", value: formatDateTime(expiresAt) }]
          : []
      }
      details={serverId ? <small>Server ID: {serverId}</small> : undefined}
    >
      {!resolved ? (
        <div className="aui-tool-card__actions">
          <Button
            type="button"
            size="sm"
            disabled={!serverId || !approvalId || pendingAction !== null}
            title={`Connect ${displayName}`}
            onClick={() => void submit("connect")}
          >
            {pendingAction === "connect" ? "Connecting..." : "Connect"}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            disabled={!serverId || !approvalId || pendingAction !== null}
            title={`Skip ${displayName} authentication`}
            onClick={() => void submit("skip")}
          >
            {pendingAction === "skip" ? "Skipping..." : "Not now"}
          </Button>
        </div>
      ) : null}
    </ActivityCard>
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

function toolDetailsContent(
  argsText: string | undefined,
  result: unknown,
): ReactNode | null {
  if (!shouldShowToolDetails(argsText, result)) {
    return null;
  }
  return (
    <>
      {argsText ? (
        <>
          <small>Input</small>
          <pre>{formatToolValue(parseToolArgs(argsText) ?? argsText)}</pre>
        </>
      ) : null}
      {result !== undefined && !largeToolResultFromValue(result) ? (
        <>
          <small>Result</small>
          <pre>{formatToolValue(displayToolResult(result))}</pre>
        </>
      ) : null}
    </>
  );
}

function approvalDetailsContent(
  args: Record<string, unknown>,
  result: unknown,
): ReactNode | null {
  const reason = stringValue(args.reason);
  const toolArgs = args.arguments;
  const renderedResult =
    result !== undefined ? (
      <>
        <small>Decision</small>
        <pre>{formatToolValue(displayToolResult(result))}</pre>
      </>
    ) : null;
  if (!reason && toolArgs === undefined && !renderedResult) {
    return null;
  }
  return (
    <>
      {reason ? (
        <>
          <small>Reason</small>
          <p>{reason}</p>
        </>
      ) : null}
      {toolArgs !== undefined ? (
        <>
          <small>Arguments</small>
          {formatDetailValue(toolArgs)}
        </>
      ) : null}
      {renderedResult}
    </>
  );
}

function shouldShowToolDetails(
  argsText: string | undefined,
  result: unknown,
): boolean {
  if (!argsText && result === undefined) {
    return false;
  }
  if (largeToolResultFromValue(result)) {
    return Boolean(argsText && hasComplexToolArgs(argsText));
  }
  return Boolean(
    (argsText && hasComplexToolArgs(argsText)) || hasComplexToolResult(result),
  );
}

function shouldRenderFullToolCard(
  status: string,
  isError: boolean | undefined,
  result: unknown,
): boolean {
  return (
    isError === true ||
    status === "requires-action" ||
    hasRichToolResult(result)
  );
}

function shouldRenderFullMcpCard(
  toolName: string,
  status: string,
  isError: boolean | undefined,
  result: unknown,
): boolean {
  if (isError === true || status === "requires-action") {
    return true;
  }
  if (status === "running") {
    return false;
  }
  return toolName === "call_mcp_tool" && hasRichToolResult(result);
}

function hasRichToolResult(value: unknown): boolean {
  if (value === undefined || value === null) {
    return false;
  }
  if (largeToolResultFromValue(value)) {
    return false;
  }
  if (Array.isArray(value)) {
    return value.length > 3;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed.length > 360 || trimmed.split(/\r\n|\r|\n/).length > 4;
  }
  const record = asRecord(value);
  const keys = Object.keys(record);
  if (keys.length === 0) {
    return false;
  }
  const output = asRecord(record.output);
  const content = output.content ?? record.content;
  const text = mcpContentText(content) ?? stringValue(output.text);
  if (text) {
    const parsed = parseJsonObject(text);
    if (Array.isArray(parsed?.results) || stringValue(parsed?.overview)) {
      return true;
    }
    return text.length > 360 || text.split(/\r\n|\r|\n/).length > 4;
  }
  const informationalKeys = new Set([
    "message",
    "content",
    "summary",
    "status",
  ]);
  return keys.some((key) => !informationalKeys.has(key));
}

function hasComplexToolArgs(argsText: string): boolean {
  const args = parseToolArgs(argsText);
  if (args === null) {
    return true;
  }
  return visibleToolArgEntries(args).some(([, value]) =>
    isComplexToolValue(value),
  );
}

function hasComplexToolResult(value: unknown): boolean {
  if (value === undefined || value === null) {
    return false;
  }
  if (Array.isArray(value)) {
    return value.length > 0;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed.length > 160 || trimmed.includes("\n");
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return false;
  }
  const record = asRecord(value);
  const keys = Object.keys(record);
  if (keys.length === 0) {
    return false;
  }
  const messageOnly = keys.every((key) =>
    ["message", "content", "summary"].includes(key),
  );
  return !messageOnly || keys.some((key) => isComplexToolValue(record[key]));
}

function isComplexToolValue(value: unknown): boolean {
  if (Array.isArray(value)) {
    return value.length > 0;
  }
  if (value && typeof value === "object") {
    return true;
  }
  if (typeof value === "string") {
    return value.length > 120 || value.includes("\n");
  }
  return false;
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

function activityParams(
  argsText: string | undefined,
  args: Record<string, unknown>,
): ActivityParam[] {
  const parsed = argsText ? parseToolArgs(argsText) : null;
  return visibleToolArgEntries(parsed ?? args)
    .slice(0, 5)
    .map(([key, value]) => ({
      label: formatArgLabel(key),
      value: formatInlineValue(value),
      block: false,
    }));
}

function mcpActivityParams(
  serverName: string | null,
  toolName: string | null,
  args: unknown,
): ActivityParam[] {
  const params: ActivityParam[] = [];
  if (serverName) {
    params.push({ label: "Server", value: humanizeIdentifier(serverName) });
  }
  if (toolName) {
    params.push({ label: "Tool", value: humanizeIdentifier(toolName) });
  }
  if (args !== undefined) {
    const displayArgs = parseJsonObject(args) ?? args;
    params.push({
      label: "Arguments",
      value: formatDetailValue(displayArgs),
      block: isComplexToolValue(displayArgs),
    });
  }
  return params;
}

function summarizeToolValue(value: unknown, toolName?: string): string {
  const largeResult = largeToolResultFromValue(value);
  if (largeResult) {
    return "Large result saved for the agent to inspect";
  }
  if (Array.isArray(value)) {
    return value.length === 0
      ? emptyResultLabel(toolName)
      : `${value.length} results`;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (trimmed === "[]") {
      return emptyResultLabel(toolName);
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

function summarizeMcpResult(value: unknown): ReactNode {
  const loadedServer = loadedMcpServerSummary(value);
  if (loadedServer) {
    return loadedServer;
  }
  const parsed = parseJsonObject(value);
  const output = asRecord(parsed?.output ?? parsed ?? value);
  const content = output.content;
  const text = mcpContentText(content) ?? stringValue(output.text);
  if (text) {
    const parsedText = parseJsonObject(text);
    const overview = stringValue(parsedText?.overview);
    const results = Array.isArray(parsedText?.results)
      ? parsedText.results
      : null;
    if (overview || results) {
      return (
        <div className="aui-mcp-result-preview">
          {overview ? <p>{overview}</p> : null}
          {results ? <McpResultList results={results} /> : null}
        </div>
      );
    }
    return summarizeInlineString(text);
  }
  return summarizeToolValue(value);
}

function McpResultList({ results }: { results: unknown[] }): ReactElement {
  const rows = results.map(asRecord).slice(0, 3);
  if (rows.length === 0) {
    return <p>No results returned.</p>;
  }
  return (
    <ul className="aui-mcp-result-preview__list">
      {rows.map((row, index) => {
        const name =
          stringValue(row.name) ?? stringValue(row.title) ?? "Result";
        const status = stringValue(row.status);
        const url = stringValue(row.url);
        return (
          <li key={`${name}-${index}`}>
            <span>{name}</span>
            {status ? <Badge tone="neutral">{status}</Badge> : null}
            {url ? (
              <a href={url} target="_blank" rel="noreferrer">
                Open
              </a>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function mcpContentText(content: unknown): string | null {
  if (typeof content === "string") {
    return content;
  }
  if (!Array.isArray(content)) {
    return null;
  }
  for (const item of content) {
    const record = asRecord(item);
    const text = stringValue(record.text);
    if (text) {
      return text;
    }
  }
  return null;
}

function parseJsonObject(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  if (typeof value !== "string") {
    return null;
  }
  try {
    return asRecord(JSON.parse(value) as unknown);
  } catch {
    return null;
  }
}

function displayToolResult(value: unknown): unknown {
  const parsed = parseJsonObject(value);
  const output = asRecord(parsed?.output ?? parsed ?? value);
  const content = output.content;
  const text = mcpContentText(content) ?? stringValue(output.text);
  if (text) {
    return parseJsonObject(text) ?? text;
  }
  return parsed ?? value;
}

function loadedMcpServerSummary(value: unknown): ReactNode | null {
  const payload = displayToolResult(value);
  const loadedServer = asRecord(asRecord(payload).loaded_server);
  if (Object.keys(loadedServer).length === 0) {
    return null;
  }
  const serverCard = asRecord(loadedServer.server_card);
  const tools = Array.isArray(loadedServer.tools) ? loadedServer.tools : [];
  const displayName =
    stringValue(serverCard.display_name) ??
    stringValue(serverCard.name) ??
    "MCP server";
  const health = stringValue(serverCard.health);
  const authState = stringValue(serverCard.auth_state);
  const visibleTools = tools
    .map((tool) => stringValue(asRecord(tool).name))
    .filter((tool): tool is string => tool !== null)
    .slice(0, 4);
  return (
    <div className="aui-mcp-result-preview">
      <p>
        Loaded {tools.length} tools from {displayName}.
      </p>
      {health || authState ? (
        <div className="aui-mcp-result-preview__badges">
          {health ? (
            <Badge tone="neutral">{humanizeIdentifier(health)}</Badge>
          ) : null}
          {authState ? (
            <Badge tone="neutral">{humanizeIdentifier(authState)}</Badge>
          ) : null}
        </div>
      ) : null}
      {visibleTools.length > 0 ? (
        <p>
          Available tools include{" "}
          {visibleTools.map(humanizeIdentifier).join(", ")}.
        </p>
      ) : null}
    </div>
  );
}

function emptyResultLabel(toolName?: string): string {
  const normalized = toolName?.toLowerCase() ?? "";
  if (normalized.includes("grep") || normalized.includes("search")) {
    return "No matches found";
  }
  if (normalized.includes("ls") || normalized.includes("list")) {
    return "No files found";
  }
  return "No results";
}

type SubagentActivityRecord = {
  id: string;
  kind: string;
  title: string;
  status: string;
  summary: string | null;
  inputSummary: string | null;
  result: string | null;
  isError: boolean;
};

function subagentActivityRecords(value: unknown): SubagentActivityRecord[] {
  return Array.isArray(value)
    ? value.map(subagentActivityRecord).filter(isSubagentActivityRecord)
    : [];
}

function subagentActivityRecord(value: unknown): SubagentActivityRecord | null {
  const record = asRecord(value);
  const id = stringValue(record.id);
  if (!id) {
    return null;
  }
  return {
    id,
    kind: stringValue(record.kind) ?? "activity",
    title: stringValue(record.title) ?? "Activity",
    status: stringValue(record.status) ?? "running",
    summary: stringValue(record.summary),
    inputSummary: stringValue(record.input_summary),
    result: stringValue(record.result),
    isError: record.is_error === true,
  };
}

function isSubagentActivityRecord(
  value: SubagentActivityRecord | null,
): value is SubagentActivityRecord {
  return value !== null;
}

function hasImportantSubagentActivity(
  activities: SubagentActivityRecord[],
): boolean {
  return activities.some(
    (activity) =>
      activity.isError ||
      !["complete", "completed"].includes(activity.status.toLowerCase()),
  );
}

function activityTitle(activity: SubagentActivityRecord): string {
  if (activity.kind === "tool") {
    return activity.isError
      ? `Could not run ${toolDisplayName(activity.title)}`
      : toolDisplayName(activity.title);
  }
  return activity.title;
}

function LargeToolResultNotice({
  compact = false,
}: {
  result: LargeToolResult;
  compact?: boolean;
}): ReactElement {
  return (
    <div className="aui-tool-card__notice">
      <span className="aui-tool-card__notice-title">Large result saved</span>
      {compact ? null : (
        <p>
          The connector returned more data than fits in chat. The agent can
          inspect the saved response when it needs details.
        </p>
      )}
    </div>
  );
}

type LargeToolResult = {
  path: string;
  callId: string | null;
};

function largeToolResultFromValue(value: unknown): LargeToolResult | null {
  const text = largeToolResultText(value);
  if (text === null) {
    return null;
  }
  const pathMatch = text.match(
    /path:\s*(\/large_tool_results\/[A-Za-z0-9_-]+)/,
  );
  if (!pathMatch) {
    return null;
  }
  const callMatch = text.match(/tool call\s+([A-Za-z0-9_-]+)/);
  return {
    path: pathMatch[1],
    callId: callMatch?.[1] ?? null,
  };
}

function largeToolResultText(value: unknown): string | null {
  if (typeof value === "string") {
    return value;
  }
  const record = asRecord(value);
  const output = asRecord(record.output);
  return (
    mcpContentText(record.content) ??
    mcpContentText(output.content) ??
    stringValue(record.content) ??
    stringValue(output.content) ??
    stringValue(output.text)
  );
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

function truncateText(value: string, maxLength: number): string {
  if (value.length <= maxLength) {
    return value;
  }
  const truncated = value.slice(0, maxLength - 3).replace(/\s+\S*$/, "");
  return `${truncated || value.slice(0, maxLength - 3)}...`;
}

function formatDetailValue(value: unknown): ReactNode {
  if (typeof value === "string") {
    const parsed = parseJsonObject(value);
    if (parsed) {
      return <pre>{formatToolValue(parsed)}</pre>;
    }
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

function toolActivityTitle(
  toolName: string,
  status: string,
  isError: boolean | undefined,
  _result: unknown,
  _summary: string | null,
): string {
  const displayName = toolDisplayName(toolName);
  if (isError || status === "incomplete") {
    return `Could not run ${displayName}`;
  }
  if (status === "requires-action") {
    return `${displayName} needs attention`;
  }
  return status === "running" ? `${displayName} running` : displayName;
}

function inlineToolTitle(
  toolName: string,
  status: string,
  isError: boolean | undefined,
): string {
  const displayName = toolDisplayName(toolName);
  if (isError || status === "incomplete") {
    return `${displayName} failed`;
  }
  return status === "running" ? `Running ${displayName}` : displayName;
}

function toolDisplayName(toolName: string): string {
  const normalized = toolName.trim().toLowerCase();
  if (normalized === "ls" || normalized === "list_files") {
    return "List directory";
  }
  if (
    normalized === "grep" ||
    normalized === "rg" ||
    normalized.includes("search")
  ) {
    return "Search files";
  }
  if (normalized === "read_file") {
    return "Read file";
  }
  if (normalized === "shell") {
    return "Run command";
  }
  return humanizeIdentifier(toolName || "tool");
}

function mcpToolTitle(toolName: string, requestedTool: string | null): string {
  if (toolName === "load_mcp_server") {
    return "Load MCP tools";
  }
  if (toolName === "auth_mcp") {
    return "Authenticate MCP server";
  }
  return requestedTool ? humanizeIdentifier(requestedTool) : "Call MCP tool";
}

function inlineMcpToolTitle(
  toolName: string,
  requestedTool: string | null,
): string {
  if (toolName === "load_mcp_server") {
    return "Load MCP tools";
  }
  if (toolName === "auth_mcp") {
    return "Authenticate connector";
  }
  return requestedTool
    ? humanizeIdentifier(requestedTool)
    : humanizeIdentifier(toolName || "MCP action");
}

function mcpToolSummary(
  toolName: string,
  status: string,
  serverName: string | null,
  requestedTool: string | null,
): string {
  const target = [serverName, requestedTool].filter(Boolean).join(" / ");
  if (status === "running") {
    if (toolName === "load_mcp_server") {
      return target
        ? `Loading available tools from ${target}.`
        : "Loading available MCP tools.";
    }
    return target ? `Executing ${target}.` : "Executing MCP tool.";
  }
  if (status === "requires-action") {
    return target
      ? `Review ${target} before execution.`
      : "Review this MCP action before execution.";
  }
  return target ? `MCP action for ${target}.` : "MCP action completed.";
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

function badgeToneForStatus(
  status: string,
): "neutral" | "success" | "warning" | "danger" | "accent" {
  const normalized = status.toLowerCase();
  if (
    normalized === "complete" ||
    normalized === "completed" ||
    normalized === "done" ||
    normalized === "resolved"
  ) {
    return "success";
  }
  if (
    normalized === "starting" ||
    normalized === "working" ||
    normalized === "still working" ||
    normalized === "waiting" ||
    normalized === "running" ||
    normalized === "action required"
  ) {
    return "warning";
  }
  if (
    normalized === "could not complete" ||
    normalized === "error" ||
    normalized === "failed"
  ) {
    return "danger";
  }
  return "neutral";
}

function humanizeIdentifier(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) {
    return "Tool";
  }
  return trimmed
    .replace(/^mcp[_-]/i, "")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatAgentName(value: string): string {
  return humanizeIdentifier(value);
}
