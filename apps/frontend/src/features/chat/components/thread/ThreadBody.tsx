import {
  SelectionToolbarPrimitive,
  ThreadPrimitive,
} from "@assistant-ui/react";
import { Message, MessageParts } from "../../runtime/components";
import type { McpServer, Skill } from "@enterprise-search/api-types";
import type { ReactElement, ReactNode } from "react";
import {
  PlanningIndicator,
  type RunIndicator,
} from "../activity/PlanningIndicator";
import {
  AssistantComposer,
  type DetailsPanelKind,
} from "../composer/AssistantComposer";
import type { ModelCatalogModel } from "@enterprise-search/api-types";
import type { ThinkingDepth } from "../../depth";
import { useAuth } from "../../../auth/AuthContext";
import { useMyProfile } from "../../../auth/useMyProfile";
import { AssistantMessage } from "../messages/AssistantMessage";
import { UserEditComposer } from "../messages/UserEditComposer";
import { UserMessage } from "../messages/UserMessage";
import { PlainText } from "../markdown/PlainText";
import { firstNameFromDisplayName } from "../../utils/greeting";
import { ThreadWelcome } from "./ThreadWelcome";

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
  onOpenDetailsPanel,
  onOpenSources,
  connectorsTrigger,
  activeModelLabel,
  models,
  selectedModel,
  onModelChange,
  depth,
  onDepthChange,
  depthVisible,
  controlsDisabled,
  onSelectSuggestion,
  onResumeToolCall,
  onReload,
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
  onOpenDetailsPanel?: (kind: DetailsPanelKind) => void;
  /**
   * PR 3.5 / G9 — opens the workspace pane on the Sources tab and scrolls
   * to the chosen citation row. Fired from MessageSourcesStrip clicks
   * inside the assistant message. Optional so non-pane mounts (storybook
   * / shared-thread preview) silently degrade.
   */
  onOpenSources?: (citationId: string) => void;
  /** PR 3.4 — slot through to the AssistantComposer's connectors trigger. */
  connectorsTrigger?: ReactNode;
  /** PR 8.0.1 — display name of the active model, surfaced in the
   * composer footer hint row. */
  activeModelLabel?: string;
  /** PR 8.0.2 — model + thinking-depth controls moved from the topbar
   * into the composer. Plumbed through the same way the composer
   * already receives `connectorsTrigger`. */
  models?: Array<ModelCatalogModel & { disabled?: boolean }>;
  selectedModel?: string;
  onModelChange?: (id: string) => void;
  depth?: ThinkingDepth;
  onDepthChange?: (depth: ThinkingDepth) => void;
  depthVisible?: boolean;
  controlsDisabled?: boolean;
  /**
   * Empty-thread suggestion picker. Routed through to `ThreadWelcome`
   * so the host (`ChatScreen`) controls how a clicked card becomes a
   * runtime append.
   */
  onSelectSuggestion?: (prompt: string) => void;
  /**
   * Tool-call interrupt resolution. Wired through `MessageContext` so
   * approval / mcp-auth / ask-a-question tool renderers can ship the
   * user's decision back to the host's resume pipeline. Optional —
   * preview / shared-thread mounts pass nothing and tools become
   * read-only.
   */
  onResumeToolCall?: (payload: unknown) => void;
  /**
   * Footer Reload button handler. The host is given the assistant
   * message id so it can resolve the parent user message and start a
   * new run. Optional — preview / shared-thread mounts hide the button.
   */
  onReload?: (assistantMessageId: string) => void;
}): ReactElement {
  // Pull the first-token of the signed-in user's display_name for the
  // welcome greeting. PR 8.0.2 — try the session identity first (cheap,
  // no fetch); fall back to the lazy `/v1/me/profile` hook (used by
  // UserCard too) so dev personas whose bearer doesn't carry
  // display_name still get a personalised greeting on first paint after
  // the profile fetch resolves.
  const auth = useAuth();
  const profile = useMyProfile();
  const greetingFirstName = firstNameFromDisplayName(
    auth.identity?.display_name ?? profile?.display_name ?? null,
  );

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
          <ThreadWelcome
            firstName={greetingFirstName}
            onSelectSuggestion={onSelectSuggestion}
          />
        </ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages>
          {({ message }) => {
            if (message.role === "user") {
              return message.composer.isEditing ? (
                <UserEditComposer />
              ) : (
                <UserMessage message={message} />
              );
            }
            if (message.role === "system") {
              return (
                <Message message={message} className="aui-system-message">
                  <MessageParts components={{ Text: PlainText }} />
                </Message>
              );
            }
            return (
              <AssistantMessage
                message={message}
                onMcpAuthConnect={onMcpAuthConnect}
                onMcpAuthSkip={onMcpAuthSkip}
                onOpenSources={onOpenSources}
                onResumeToolCall={onResumeToolCall}
                onReload={onReload ? () => onReload(message.id) : undefined}
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
        <ThreadPrimitive.ScrollToBottom
          className="aui-scroll-bottom"
          title="Scroll to bottom"
        >
          Scroll to bottom
        </ThreadPrimitive.ScrollToBottom>
      </ThreadPrimitive.Viewport>
      <div className="aui-thread-footer">
        <AssistantComposer
          connectors={connectors}
          skills={skills}
          onOpenMcpSettings={onOpenMcpSettings}
          onOpenSkillsSettings={onOpenSkillsSettings}
          onShowConnectors={onShowConnectors}
          onOpenDetailsPanel={onOpenDetailsPanel}
          connectorsTrigger={connectorsTrigger}
          activeModelLabel={activeModelLabel}
          models={models}
          selectedModel={selectedModel}
          onModelChange={onModelChange}
          depth={depth}
          onDepthChange={onDepthChange}
          depthVisible={depthVisible}
          controlsDisabled={controlsDisabled}
        />
      </div>
    </ThreadPrimitive.Root>
  );
}
