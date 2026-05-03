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
import type { AnchorHTMLAttributes, ReactElement, ReactNode } from "react";
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
  RuntimeEventPresentation,
} from "@enterprise-search/api-types";
import { isAssistantPerformanceMetrics } from "@enterprise-search/api-types";
import { markdownLinkLabel } from "./markdownLinks";
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
  connectors,
  skills,
  connectorSuggestions,
  runIndicator,
  onMcpAuthConnect,
  onMcpAuthSkip,
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
  connectorSuggestions: ReactNode;
  runIndicator: RunIndicator | null;
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
        {runIndicator ? (
          <PlanningIndicator
            label={runIndicator.label}
            visible={runIndicator.visible}
          />
        ) : null}
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

type RunIndicator = {
  label: string;
  visible: boolean;
};

function PlanningIndicator({ label, visible }: RunIndicator): ReactElement {
  const words = label.split(" ");
  return (
    <div
      className="aui-planning-indicator"
      data-visible={visible ? "true" : "false"}
      role={visible ? "status" : undefined}
      aria-live={visible ? "polite" : undefined}
      aria-hidden={visible ? undefined : "true"}
      aria-label={label}
    >
      <span className="aui-planning-indicator__text" aria-hidden="true">
        {words.map((word, index) => (
          <span
            className={classNames(
              "aui-planning-indicator__word",
              `aui-planning-indicator__word--${index + 1}`,
            )}
            key={`${word}-${index}`}
          >
            {word}
          </span>
        ))}
      </span>
    </div>
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
  detailsLabel = "Tool details",
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
  detailsLabel?: string;
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
      {details ? (
        <ActivityDetails label={detailsLabel}>{details}</ActivityDetails>
      ) : null}
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

function ActivityDetails({
  children,
  label = "Tool details",
}: {
  children: ReactNode;
  label?: string;
}): ReactElement {
  return (
    <ActivityCollapsible
      className="aui-activity-card__details"
      contentClassName="aui-activity-card__details-content"
      label={label}
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
  detailsLabel = "Details",
  result,
  icon,
}: {
  title: string;
  status: string;
  variant?: ActivityVariant;
  description?: ReactNode;
  details?: ReactNode;
  detailsLabel?: string;
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
          label={detailsLabel}
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

function GeneratedPresentationCard({
  presentation,
  details,
  forceCard = false,
  variant,
}: {
  presentation: RuntimeEventPresentation;
  details?: ReactNode;
  forceCard?: boolean;
  variant?: ActivityVariant;
}): ReactElement {
  const result =
    presentation.result_preview && presentation.result_preview.length > 0 ? (
      <PresentationResultRows rows={presentation.result_preview} />
    ) : undefined;
  const cardVariant = variant ?? activityVariantForPresentation(presentation);
  if (!forceCard && presentation.kind === "progress") {
    return (
      <ActivityItem
        title={presentation.title}
        status={presentation.status_label}
        variant={cardVariant}
        description={presentation.summary}
        result={result}
        details={details}
        detailsLabel={presentation.debug_label ?? "Tool details"}
      />
    );
  }
  return (
    <ActivityCard
      title={presentation.title}
      status={presentation.status_label}
      variant={cardVariant}
      description={presentation.summary}
      result={result}
      details={details}
      detailsLabel={presentation.debug_label ?? "Tool details"}
    />
  );
}

function PresentationResultRows({
  rows,
}: {
  rows: RuntimeEventPresentation["result_preview"];
}): ReactElement {
  return (
    <div className="aui-presentation-result">
      {rows?.map((row, index) => (
        <div
          className="aui-presentation-result__row"
          key={`${row.title}-${index}`}
        >
          <div className="aui-presentation-result__text">
            {row.url ? (
              <a href={row.url} target="_blank" rel="noreferrer">
                {row.title}
              </a>
            ) : (
              <span>{row.title}</span>
            )}
            {row.subtitle ? <p>{row.subtitle}</p> : null}
          </div>
          {row.badge ? <Badge>{row.badge}</Badge> : null}
        </div>
      ))}
    </div>
  );
}

function activityVariantForPresentation(
  presentation: RuntimeEventPresentation,
): ActivityVariant {
  if (presentation.kind === "approval") {
    return "approval";
  }
  if (presentation.kind === "auth") {
    return "connector";
  }
  if (presentation.kind === "progress") {
    return "progress";
  }
  return "tool";
}

function presentationFromArgs(
  args: Record<string, unknown>,
): RuntimeEventPresentation | null {
  const raw = asRecord(args.presentation);
  const title = stringValue(raw.title);
  const status = stringValue(raw.status_label);
  const kind = stringValue(raw.kind);
  if (!title || !status || !kind) {
    return null;
  }
  return {
    title,
    summary: stringValue(raw.summary),
    status_label: status as RuntimeEventPresentation["status_label"],
    kind: kind as RuntimeEventPresentation["kind"],
    group_key: stringValue(raw.group_key),
    primary_entity: stringValue(raw.primary_entity),
    action_label: stringValue(raw.action_label),
    result_preview: presentationRows(raw.result_preview),
    debug_label: stringValue(raw.debug_label),
    confidence: stringValue(
      raw.confidence,
    ) as RuntimeEventPresentation["confidence"],
  };
}

function presentationRows(
  value: unknown,
): RuntimeEventPresentation["result_preview"] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((item) => {
    const row = asRecord(item);
    const title = stringValue(row.title);
    if (!title) {
      return [];
    }
    return [
      {
        title,
        subtitle: stringValue(row.subtitle),
        url: stringValue(row.url),
        badge: stringValue(row.badge),
      },
    ];
  });
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

function MarkdownText({ text, status }: TextMessagePartProps): ReactElement {
  const streaming = status.type === "running";
  return (
    <Streamdown
      animated={
        streaming
          ? {
              animation: "fadeIn",
              duration: 120,
              easing: "ease-out",
              sep: "word",
            }
          : false
      }
      className={classNames(
        "assistant-markdown",
        streaming ? "assistant-markdown--streaming" : undefined,
      )}
      components={markdownComponents}
      isAnimating={streaming}
      mode={streaming ? "streaming" : "static"}
    >
      {text}
    </Streamdown>
  );
}

const markdownComponents = {
  a: MarkdownLink,
};

function MarkdownLink({
  children,
  href,
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement>): ReactElement {
  const external = isExternalHref(href);
  return (
    <a
      {...props}
      href={href}
      rel={external ? "noreferrer" : props.rel}
      target={external ? "_blank" : props.target}
      title={props.title ?? (typeof href === "string" ? href : undefined)}
    >
      {markdownLinkLabel(href, children)}
    </a>
  );
}

function isExternalHref(href: string | undefined): boolean {
  return Boolean(href && /^https?:\/\//i.test(href));
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
  const presentation = presentationFromArgs(args);
  const argsSummary = summarizeArgsText(argsText);
  const activitySummary = stringValue(args.summary) ?? argsSummary;
  const statusLabel = toolStatusLabel(status.type, isError);
  const largeResult = largeToolResultFromValue(result);
  const title = inlineToolTitle(toolName, status.type, isError, result);
  const resultSummary = largeResult
    ? "large result saved"
    : result !== undefined
      ? safeMainResultSummary(summarizeToolValue(result, toolName))
      : undefined;
  const details = toolDetailsContent(argsText, result);
  if (presentation) {
    return (
      <GeneratedPresentationCard
        presentation={presentation}
        details={details}
        forceCard={shouldRenderFullToolCard(status.type, isError, result)}
      />
    );
  }
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
  const presentation = presentationFromArgs(args);
  const serverName = stringValue(args.server_name);
  const displayName = safeConnectorDisplayName(
    stringValue(args.display_name) ?? serverName,
  );
  const requestedTool = stringValue(args.tool_name);
  const resultNotice = largeToolResultFromValue(result);
  const statusLabel = toolStatusLabel(status.type, isError);
  const title = inlineMcpToolTitle(
    toolName,
    requestedTool,
    displayName,
    status.type,
  );
  const description = mcpToolSummary(
    toolName,
    status.type,
    displayName,
    requestedTool,
  );
  const resultSummary = resultNotice
    ? "large result saved"
    : result !== undefined
      ? safeMainResultSummary(summarizeMcpResult(result))
      : undefined;
  const details = toolDetailsContent(argsText, result);
  if (presentation) {
    return (
      <GeneratedPresentationCard
        presentation={presentation}
        details={details}
        forceCard={shouldRenderFullMcpCard(
          toolName,
          status.type,
          isError,
          result,
        )}
        variant="mcp"
      />
    );
  }
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
      params={mcpActivityParams(displayName, requestedTool, args.arguments)}
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
  const presentation = presentationFromArgs(data);
  if (presentation) {
    return (
      <GeneratedPresentationCard
        presentation={presentation}
        details={toolDetailsContent(props.argsText, props.result)}
      />
    );
  }
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
  const presentation = presentationFromArgs(args);
  const approvalId = String(args.approval_id ?? "");
  const toolName = stringValue(args.tool_name);
  const serverName = stringValue(args.server_name);
  const displayName =
    safeConnectorDisplayName(stringValue(args.display_name) ?? serverName) ??
    (serverName
      ? safeConnectorDisplayName(humanizeIdentifier(serverName))
      : null);
  const riskLevel = stringValue(args.risk_level);
  const readOnly = typeof args.read_only === "boolean" ? args.read_only : null;
  const isMcpApproval =
    stringValue(args.approval_kind) === "mcp_tool" ||
    stringValue(args.kind) === "mcp_tool";
  const resolved = result !== undefined;
  const submit = (decision: ApprovalDecision): void => {
    resume({ decision, approval_id: approvalId });
  };
  const approvalStatus = resolved ? "Done" : "Waiting for permission";
  const actionName = toolActionName(toolName);
  const approvalTitle = resolved
    ? isMcpApproval
      ? "Permission approved"
      : "Approval resolved"
    : isMcpApproval
      ? `Allow ${displayName ?? "connector"} ${actionName}?`
      : "Approval requested";
  const approvalDescription = isMcpApproval
    ? mcpApprovalDescription(displayName, actionName, readOnly, args.message)
    : String(args.message ?? args.reason ?? approvalId);
  const cardTitle = presentation?.title ?? approvalTitle;
  const cardDescription = presentation?.summary ?? approvalDescription;
  const cardStatus = presentation?.status_label ?? approvalStatus;
  const cardResult =
    presentation?.result_preview && presentation.result_preview.length > 0 ? (
      <PresentationResultRows rows={presentation.result_preview} />
    ) : undefined;
  return (
    <ActivityCard
      title={cardTitle}
      status={cardStatus}
      variant="approval"
      description={cardDescription}
      params={
        isMcpApproval
          ? [
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
      result={cardResult}
      details={approvalDetailsContent(args, result)}
      detailsLabel={presentation?.debug_label ?? "Tool details"}
    >
      {!resolved ? (
        <div className="aui-tool-card__actions">
          <Button
            type="button"
            size="sm"
            title={isMcpApproval ? "Allow this connector action" : "Approve"}
            onClick={() => submit("approved")}
          >
            {isMcpApproval ? "Allow once" : "Approve"}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            title={isMcpApproval ? "Deny this connector action" : "Reject"}
            onClick={() => submit("rejected")}
          >
            {isMcpApproval ? "Deny" : "Reject"}
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
  const presentation = presentationFromArgs(args);
  const [pendingAction, setPendingAction] = useState<"connect" | "skip" | null>(
    null,
  );
  const serverId = stringValue(args.server_id);
  const approvalId =
    stringValue(args.approval_id) ?? stringValue(args.action_id) ?? serverId;
  const displayName =
    safeConnectorDisplayName(
      stringValue(args.display_name) ?? stringValue(args.server_name),
    ) ?? "connector";
  const message =
    stringValue(args.message) ??
    `Enterprise Search needs permission to use ${displayName}.`;
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
      title={
        presentation?.title ??
        (resolved ? `${displayName} connected` : `Connect ${displayName}`)
      }
      status={
        presentation?.status_label ??
        (resolved ? "Done" : "Waiting for permission")
      }
      variant="connector"
      description={presentation?.summary ?? message}
      params={
        expiresAt
          ? [{ label: "Link expires", value: formatDateTime(expiresAt) }]
          : []
      }
      details={mcpAuthDetails(args, result)}
      detailsLabel={presentation?.debug_label ?? "Tool details"}
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
          <pre>{formatToolValue(parseJsonValue(argsText) ?? argsText)}</pre>
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
  const debug = compactRecord({
    server_id: args.server_id,
    server_name: args.server_name,
    tool_name: args.tool_name,
    approval_id: args.approval_id,
  });
  const renderedResult =
    result !== undefined ? (
      <>
        <small>Decision</small>
        <pre>{formatToolValue(displayToolResult(result))}</pre>
      </>
    ) : null;
  if (!reason && toolArgs === undefined && !renderedResult && !debug) {
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
      {hasVisibleValue(toolArgs) ? (
        <>
          <small>Arguments</small>
          {formatDetailValue(toolArgs)}
        </>
      ) : null}
      {debug ? (
        <>
          <small>Debug</small>
          <pre>{formatToolValue(debug)}</pre>
        </>
      ) : null}
      {renderedResult}
    </>
  );
}

function mcpAuthDetails(
  args: Record<string, unknown>,
  result: unknown,
): ReactNode | null {
  const debug = compactRecord({
    server_id: args.server_id,
    server_name: args.server_name,
    approval_id: args.approval_id ?? args.action_id,
  });
  const renderedResult =
    result !== undefined ? (
      <>
        <small>Result</small>
        <pre>{formatToolValue(displayToolResult(result))}</pre>
      </>
    ) : null;
  if (!debug && !renderedResult) {
    return null;
  }
  return (
    <>
      {debug ? (
        <>
          <small>Debug</small>
          <pre>{formatToolValue(debug)}</pre>
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
    params.push({ label: "App", value: safeConnectorDisplayName(serverName) });
  }
  if (toolName) {
    params.push({ label: "Action", value: safeToolActionLabel(toolName) });
  }
  if (args !== undefined && hasVisibleValue(args)) {
    const displayArgs = parseJsonValue(args) ?? args;
    if (!isComplexToolValue(displayArgs)) {
      params.push({
        label: "Input",
        value: formatInlineValue(displayArgs),
        block: false,
      });
    }
  }
  return params;
}

function safeMainResultSummary(value: ReactNode): ReactNode {
  if (typeof value !== "string") {
    return value;
  }
  if (value.includes("/large_tool_results/")) {
    return "Large result saved for internal inspection";
  }
  const parsed = parseJsonValue(value);
  if (parsed !== null) {
    return summarizeParsedMainResult(parsed);
  }
  if (value.length > 220 || value.split(/\r\n|\r|\n/).length > 3) {
    return summarizeInlineString(value);
  }
  return safeVisibleText(value);
}

function summarizeParsedMainResult(value: unknown): string {
  if (Array.isArray(value)) {
    return value.length === 0 ? "No results" : `${value.length} results`;
  }
  const record = asRecord(value);
  const rows = resultRowsFromValue(record);
  if (rows) {
    return `${rows.length} results`;
  }
  const message =
    stringValue(record.message) ??
    stringValue(record.summary) ??
    stringValue(record.overview);
  return message ? safeVisibleText(message) : "Result returned";
}

function safeConnectorDisplayName(value: string | null): string | null {
  if (!value) {
    return null;
  }
  return safeVisibleText(humanizeIdentifier(value));
}

function safeToolActionLabel(value: string): string {
  const normalized = value
    .replace(/^call_mcp_tool$/i, "action")
    .replace(/^auth_mcp$/i, "connect")
    .replace(/^mcp_/i, "")
    .replace(/_com$/i, "")
    .replace(/^[a-z0-9]+_/, "");
  return safeVisibleText(humanizeIdentifier(normalized));
}

function safeVisibleText(value: string): string {
  return value
    .replaceAll("/large_tool_results/", "saved result ")
    .replace(/\bmcp[_-]/gi, "")
    .replace(/_com\b/gi, "")
    .replaceAll("_", " ")
    .replace(/\s+/g, " ")
    .trim();
}

function summarizeToolValue(value: unknown, toolName?: string): ReactNode {
  const largeResult = largeToolResultFromValue(value);
  if (largeResult) {
    return "Large result saved for the agent to inspect";
  }
  const sources = searchSourcesFromValue(value);
  if (isWebSearchTool(toolName) && sources) {
    return <SearchSourceList sources={sources} />;
  }
  const normalizedValue = parseJsonValue(value) ?? value;
  if (Array.isArray(normalizedValue)) {
    return normalizedValue.length === 0
      ? emptyResultLabel(toolName)
      : `${normalizedValue.length} results`;
  }
  if (typeof normalizedValue === "string") {
    const trimmed = normalizedValue.trim();
    if (trimmed === "[]") {
      return emptyResultLabel(toolName);
    }
    return trimmed || "Completed";
  }
  const record = asRecord(normalizedValue);
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
  const genericResults = resultRowsFromValue(value);
  if (genericResults) {
    return <McpResultList results={genericResults} />;
  }
  return summarizeToolValue(value);
}

function SearchSourceList({
  sources,
}: {
  sources: SearchSource[];
}): ReactElement {
  const rows = sources.slice(0, 4);
  return (
    <div className="aui-mcp-result-preview">
      <p>{sources.length} sources found</p>
      <ul className="aui-mcp-result-preview__list">
        {rows.map((source, index) => (
          <li key={`${source.link ?? source.title}-${index}`}>
            <span className="aui-mcp-result-preview__primary">
              <span>{source.title}</span>
              {source.snippet ? (
                <small>{truncateText(source.snippet, 150)}</small>
              ) : null}
            </span>
            {source.trust ? <Badge tone="neutral">{source.trust}</Badge> : null}
            {source.link ? (
              <a href={source.link} target="_blank" rel="noreferrer">
                Open
              </a>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
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

type SearchSource = {
  title: string;
  link: string | null;
  snippet: string | null;
  trust: string | null;
};

function searchSourcesFromValue(value: unknown): SearchSource[] | null {
  const rows = resultRowsFromValue(value);
  if (!rows) {
    return null;
  }
  const sources = rows
    .map((row) => {
      const title =
        stringValue(row.title) ??
        stringValue(row.name) ??
        stringValue(row.url) ??
        stringValue(row.link);
      if (!title) {
        return null;
      }
      const link = stringValue(row.link) ?? stringValue(row.url);
      return {
        title,
        link,
        snippet:
          stringValue(row.snippet) ??
          stringValue(row.description) ??
          stringValue(row.content),
        trust: sourceTrustLabel(title, link),
      };
    })
    .filter((source): source is SearchSource => source !== null);
  return sources.length > 0 ? sources : null;
}

function resultRowsFromValue(value: unknown): Record<string, unknown>[] | null {
  const parsed = parseJsonValue(value) ?? value;
  if (Array.isArray(parsed)) {
    const rows = parsed
      .map(asRecord)
      .filter((row) => Object.keys(row).length > 0);
    return rows.length > 0 ? rows : null;
  }
  const record = asRecord(parsed);
  for (const candidate of [
    record.results,
    record.items,
    record.sources,
    asRecord(record.output).results,
    asRecord(record.output).items,
  ]) {
    if (Array.isArray(candidate)) {
      const rows = candidate
        .map(asRecord)
        .filter((row) => Object.keys(row).length > 0);
      if (rows.length > 0) {
        return rows;
      }
    }
  }
  const text =
    mcpContentText(record.content) ??
    stringValue(record.text) ??
    stringValue(asRecord(record.output).text);
  if (text) {
    const parsedText = parseJsonValue(text);
    if (Array.isArray(parsedText)) {
      const rows = parsedText
        .map(asRecord)
        .filter((row) => Object.keys(row).length > 0);
      return rows.length > 0 ? rows : null;
    }
    const parsedRecord = asRecord(parsedText);
    if (Array.isArray(parsedRecord.results)) {
      const rows = parsedRecord.results
        .map(asRecord)
        .filter((row) => Object.keys(row).length > 0);
      return rows.length > 0 ? rows : null;
    }
  }
  return null;
}

function sourceTrustLabel(title: string, link: string | null): string | null {
  const combined = `${title} ${link ?? ""}`.toLowerCase();
  if (
    combined.includes("docs.slack.dev") ||
    combined.includes("slack.com/help") ||
    combined.includes("modelcontextprotocol.io")
  ) {
    return "Official";
  }
  if (combined.includes("github.com")) {
    return "Community";
  }
  return null;
}

function parseJsonValue(value: unknown): unknown | null {
  if (typeof value !== "string") {
    return null;
  }
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return null;
  }
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

function compactRecord(
  record: Record<string, unknown>,
): Record<string, unknown> | null {
  const entries = Object.entries(record).filter(([, value]) =>
    hasVisibleValue(value),
  );
  return entries.length > 0 ? Object.fromEntries(entries) : null;
}

function hasVisibleValue(value: unknown): boolean {
  if (value === undefined || value === null) {
    return false;
  }
  if (typeof value === "string") {
    return value.trim().length > 0;
  }
  if (Array.isArray(value)) {
    return value.length > 0;
  }
  if (typeof value === "object") {
    return Object.keys(value).length > 0;
  }
  return true;
}

function displayToolResult(value: unknown): unknown {
  const parsed = parseJsonValue(value) ?? value;
  const parsedRecord = asRecord(parsed);
  const output = asRecord(parsedRecord.output ?? parsed);
  const content = output.content;
  const text = mcpContentText(content) ?? stringValue(output.text);
  if (text) {
    return parseJsonValue(text) ?? text;
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
    const path = largeToolResultPath(value);
    return path ? { path, callId: null } : null;
  }
  const pathMatch = text.match(/(\/large_tool_results\/[A-Za-z0-9_-]+)/);
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

function largeToolResultPath(value: unknown): string | null {
  if (typeof value === "string") {
    const match = value.match(/(\/large_tool_results\/[A-Za-z0-9_-]+)/);
    return match?.[1] ?? null;
  }
  if (Array.isArray(value)) {
    for (const item of value) {
      const path = largeToolResultPath(item);
      if (path) {
        return path;
      }
    }
    return null;
  }
  if (value && typeof value === "object") {
    for (const entry of Object.values(value as Record<string, unknown>)) {
      const path = largeToolResultPath(entry);
      if (path) {
        return path;
      }
    }
  }
  return null;
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
  "action_id",
  "approval_id",
  "approval_kind",
  "auth_url",
  "display_name",
  "native_interrupt_id",
  "presentation",
  "server_id",
  "server_name",
  "source_tool_call_id",
  "tool_name",
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
  result: unknown,
): string {
  const displayName = toolDisplayName(toolName);
  if (isError || status === "incomplete") {
    return `${displayName} failed`;
  }
  if (status === "running") {
    return toolRunningTitle(toolName, displayName);
  }
  if (result !== undefined) {
    return toolCompletedTitle(toolName, displayName);
  }
  return displayName;
}

function toolDisplayName(toolName: string): string {
  const normalized = toolName.trim().toLowerCase();
  if (normalized === "ls" || normalized === "list_files") {
    return "List files";
  }
  if (isWebSearchTool(normalized)) {
    return "Search web";
  }
  if (
    normalized === "grep" ||
    normalized === "rg" ||
    normalized === "search_files" ||
    normalized === "file_search" ||
    normalized === "list_files"
  ) {
    return "Search project files";
  }
  if (normalized === "read_file") {
    return "Read file";
  }
  if (normalized === "shell") {
    return "Run command";
  }
  return humanizeIdentifier(toolName || "tool");
}

function toolRunningTitle(toolName: string, displayName: string): string {
  const normalized = toolName.trim().toLowerCase();
  if (isWebSearchTool(normalized)) {
    return "Searching the web";
  }
  if (isProjectSearchTool(normalized)) {
    return "Searching project files";
  }
  if (normalized === "ls" || normalized === "list_files") {
    return "Listing files";
  }
  return `Running ${displayName}`;
}

function toolCompletedTitle(toolName: string, displayName: string): string {
  const normalized = toolName.trim().toLowerCase();
  if (isWebSearchTool(normalized)) {
    return "Searched the web";
  }
  if (isProjectSearchTool(normalized)) {
    return "Searched project files";
  }
  if (normalized === "ls" || normalized === "list_files") {
    return "Listed files";
  }
  return displayName;
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
  displayName: string | null,
  status: string,
): string {
  if (toolName === "load_mcp_server") {
    return displayName ? `Load ${displayName} tools` : "Load connector tools";
  }
  if (toolName === "auth_mcp") {
    return displayName ? `Connect ${displayName}` : "Connect connector";
  }
  const action = toolActionName(requestedTool ?? toolName);
  const connector = displayName ?? "connector";
  if (status === "running") {
    return `${capitalize(action)} ${connector}`;
  }
  return `${capitalizePastTense(action)} ${connector}`;
}

function mcpToolSummary(
  toolName: string,
  status: string,
  serverName: string | null,
  requestedTool: string | null,
): string {
  const connector = serverName ? humanizeIdentifier(serverName) : "connector";
  const action = toolActionName(requestedTool ?? toolName);
  if (status === "running") {
    if (toolName === "load_mcp_server") {
      return `Loading available tools from ${connector}.`;
    }
    return `${capitalize(action)} ${connector}.`;
  }
  if (status === "requires-action") {
    return `Review ${connector} ${action} before it runs.`;
  }
  return `${connector} action completed.`;
}

function toolStatusLabel(status: string, isError?: boolean): string {
  if (isError) {
    return "Failed";
  }
  if (status === "requires-action") {
    return "Waiting for permission";
  }
  if (status === "running") {
    return "Running";
  }
  return "Done";
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
    normalized === "action required" ||
    normalized === "waiting for permission"
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
  const normalized = trimmed
    .replace(/^mcp[_-]/i, "")
    .replace(/[_-]mcp$/i, "")
    .replace(/\bcom$/i, "")
    .replace(/[_-]+/g, " ")
    .trim();
  return normalized
    .split(/\s+/)
    .map(formatBrandWord)
    .join(" ")
    .replace(/\bMcp\b/g, "MCP")
    .replace(/\bApi\b/g, "API")
    .replace(/\bUrl\b/g, "URL");
}

function formatBrandWord(value: string): string {
  const brands: Record<string, string> = {
    clickup: "ClickUp",
    github: "GitHub",
    gitlab: "GitLab",
    slack: "Slack",
    google: "Google",
  };
  const normalized = value.toLowerCase();
  return (
    brands[normalized] ?? value.replace(/^\w/, (letter) => letter.toUpperCase())
  );
}

function formatAgentName(value: string): string {
  return humanizeIdentifier(value);
}

function isWebSearchTool(toolName: string | undefined): boolean {
  const normalized = toolName?.trim().toLowerCase() ?? "";
  return (
    normalized === "web_search" ||
    normalized === "duckduckgo_search" ||
    normalized === "duckduckgo_search_results" ||
    normalized === "search_web"
  );
}

function isProjectSearchTool(toolName: string): boolean {
  const normalized = toolName.trim().toLowerCase();
  return (
    normalized === "grep" ||
    normalized === "rg" ||
    normalized === "search_files" ||
    normalized === "file_search"
  );
}

function toolActionName(toolName: string | null): string {
  const normalized = toolName?.trim().toLowerCase() ?? "";
  if (!normalized) {
    return "action";
  }
  if (
    normalized.includes("search") ||
    normalized.includes("filter") ||
    normalized.includes("find") ||
    normalized.includes("list")
  ) {
    return "search";
  }
  if (normalized.includes("read") || normalized.includes("get")) {
    return "read";
  }
  if (
    normalized.includes("create") ||
    normalized.includes("post") ||
    normalized.includes("send") ||
    normalized.includes("update") ||
    normalized.includes("delete")
  ) {
    return "modify";
  }
  return "action";
}

function mcpApprovalDescription(
  displayName: string | null,
  actionName: string,
  readOnly: boolean | null,
  fallback: unknown,
): string {
  const connector = displayName ?? "this connector";
  if (readOnly === true) {
    return `Enterprise Search wants to ${actionName} ${connector}. Read-only. No changes will be made.`;
  }
  if (readOnly === false) {
    return `Enterprise Search wants to ${actionName} ${connector}. This action may change data.`;
  }
  return (
    stringValue(fallback) ??
    `Enterprise Search wants to run a ${connector} action.`
  );
}

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function capitalizePastTense(value: string): string {
  if (value === "search") {
    return "Searched";
  }
  if (value === "read") {
    return "Read";
  }
  if (value === "modify") {
    return "Updated";
  }
  return "Ran";
}
