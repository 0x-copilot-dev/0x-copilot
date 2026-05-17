import type { McpServer, Skill } from "@enterprise-search/api-types";
import { forwardRef, useState, type ReactElement, type ReactNode } from "react";
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
import { PlainText } from "@enterprise-search/chat-surface";
import { firstNameFromDisplayName } from "../../utils/greeting";
import {
  Composer,
  Message,
  MessageParts,
  ThreadEmpty,
  ThreadMessages,
  ThreadRoot,
  ThreadScrollToBottom,
  ThreadViewport,
  type ComposerHandle,
} from "../../runtime";
import type {
  AttachmentAdapter,
  CompleteAttachment,
  ThreadMessageLike,
} from "../../runtime/types";
import { ThreadWelcome } from "./ThreadWelcome";

// `Composer` is referenced in the type signature for the forwarded ref
// but the value isn't used directly here. Suppress the no-unused-import
// hint without changing the surface.
void Composer;

export interface ThreadBodyHandle {
  composerHandle: ComposerHandle | null;
}

export const ThreadBody = forwardRef<
  ComposerHandle,
  {
    messages: readonly ThreadMessageLike[];
    /** Whether a run is currently in flight. Drives Composer Send/Stop. */
    running?: boolean;
    /** Composer disabled when there's no active conversation. */
    disabled?: boolean;
    /** Adapter for composer attachments. */
    attachmentAdapter?: AttachmentAdapter;
    /** Id of the user message currently being inline-edited. */
    editingMessageId?: string | null;
    onEditCancel?: () => void;
    onEditSave?: (sourceMessageId: string, text: string) => void;
    /** Composer submission. Called with finalised attachments. */
    onSubmit?: (payload: {
      text: string;
      attachments: ReadonlyArray<CompleteAttachment>;
    }) => void | Promise<void>;
    /** Stop-run handler. */
    onCancel?: () => void;
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
    /** PR 4.4.7 Phase 2 (Slice C) — discovery card for an uninstalled
     *  catalog entry routes here instead of OAuth. Optional so older
     *  test harnesses keep working. */
    onMcpInstallCatalog?: (payload: {
      slug: string;
      requiresPreRegisteredClient: boolean;
      approvalId: string;
      serverId: string;
    }) => void;
    /** PR 4.4.7 Phase 2 — Skip on a catalog suggestion writes the
     *  user's discoverable preference so the agent never resurfaces
     *  the same suggestion in a future run. */
    onMcpMuteCatalogSuggestion?: (payload: { slug: string }) => void;
    onOpenMcpSettings: () => void;
    onOpenSkillsSettings: () => void;
    onShowConnectors: () => void;
    onOpenDetailsPanel?: (kind: DetailsPanelKind) => void;
    onOpenSkillsPanel?: () => void;
    selectedSkills?: readonly Skill[];
    onAttachSkill?: (skill: Skill) => void;
    onRemoveSkill?: (skillId: string) => void;
    onClearSkills?: () => void;
    onOpenSources?: (citationId: string) => void;
    connectorsTrigger?: ReactNode;
    activeModelLabel?: string;
    models?: Array<ModelCatalogModel & { disabled?: boolean }>;
    selectedModel?: string;
    onModelChange?: (id: string) => void;
    depth?: ThinkingDepth;
    onDepthChange?: (depth: ThinkingDepth) => void;
    depthVisible?: boolean;
    controlsDisabled?: boolean;
    onSelectSuggestion?: (prompt: string) => void;
    onResumeToolCall?: (payload: unknown) => void;
    onReload?: (assistantMessageId: string) => void;
    /**
     * PR 4.4.6.4 — invoked when the user clicks Undo on an approved +
     * reversible approval receipt. Forwarded to ``ApprovalTool`` via
     * ``AssistantMessage``.
     */
    onRequestUndo?: (
      approvalId: string,
    ) => Promise<{ undo_requested_at: string }>;
  }
>(function ThreadBody(
  {
    messages,
    running = false,
    disabled = false,
    attachmentAdapter,
    editingMessageId,
    onEditCancel,
    onEditSave,
    onSubmit,
    onCancel,
    connectors,
    skills,
    connectorSuggestions,
    runIndicator,
    onMcpAuthConnect,
    onMcpAuthSkip,
    onMcpInstallCatalog,
    onMcpMuteCatalogSuggestion,
    onOpenMcpSettings,
    onOpenSkillsSettings,
    onShowConnectors,
    onOpenDetailsPanel,
    onOpenSkillsPanel,
    selectedSkills,
    onAttachSkill,
    onRemoveSkill,
    onClearSkills,
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
    onRequestUndo,
  },
  composerRef,
): ReactElement {
  const auth = useAuth();
  const profile = useMyProfile();
  const greetingFirstName = firstNameFromDisplayName(
    auth.identity?.display_name ?? profile?.display_name ?? null,
  );
  const [atBottom, setAtBottom] = useState(true);
  const isEmpty = messages.length === 0;

  // Build a scroll-key that invalidates whenever the message list grows
  // or the trailing message's content length changes (covers streaming
  // text deltas, tool-call arrivals, etc.).
  const tail = messages[messages.length - 1];
  const tailLen =
    typeof tail?.content === "string"
      ? tail.content.length
      : Array.isArray(tail?.content)
        ? tail!.content.length
        : 0;
  const scrollKey = `${messages.length}:${tailLen}:${running ? 1 : 0}`;

  return (
    <ThreadRoot className="aui-thread-root">
      <ThreadViewport
        className="aui-thread-viewport"
        scrollKey={scrollKey}
        onAtBottomChange={setAtBottom}
      >
        <ThreadEmpty isEmpty={isEmpty}>
          <ThreadWelcome
            firstName={greetingFirstName}
            onSelectSuggestion={onSelectSuggestion}
          />
        </ThreadEmpty>
        <ThreadMessages messages={messages} editingMessageId={editingMessageId}>
          {({ message, isEditing }) => {
            if (message.role === "user") {
              return isEditing ? (
                <UserEditComposer
                  message={message}
                  onCancel={() => onEditCancel?.()}
                  onSave={(text) =>
                    message.id !== undefined && onEditSave?.(message.id, text)
                  }
                />
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
                onMcpInstallCatalog={onMcpInstallCatalog}
                onMcpMuteCatalogSuggestion={onMcpMuteCatalogSuggestion}
                onOpenSources={onOpenSources}
                onResumeToolCall={onResumeToolCall}
                onReload={
                  onReload && message.id !== undefined
                    ? () => onReload(message.id as string)
                    : undefined
                }
                onRequestUndo={onRequestUndo}
              />
            );
          }}
        </ThreadMessages>
        {connectorSuggestions}
        {runIndicator ? (
          <PlanningIndicator
            label={runIndicator.label}
            visible={runIndicator.visible}
          />
        ) : null}
        <ThreadScrollToBottom
          className="aui-scroll-bottom"
          title="Scroll to bottom"
          visible={!atBottom}
        >
          Scroll to bottom
        </ThreadScrollToBottom>
      </ThreadViewport>
      <div className="aui-thread-footer">
        <AssistantComposer
          ref={composerRef}
          connectors={connectors}
          skills={skills}
          attachmentAdapter={attachmentAdapter}
          onOpenMcpSettings={onOpenMcpSettings}
          onOpenSkillsSettings={onOpenSkillsSettings}
          onShowConnectors={onShowConnectors}
          onOpenDetailsPanel={onOpenDetailsPanel}
          onOpenSkillsPanel={onOpenSkillsPanel}
          selectedSkills={selectedSkills}
          onAttachSkill={onAttachSkill}
          onRemoveSkill={onRemoveSkill}
          onClearSkills={onClearSkills}
          connectorsTrigger={connectorsTrigger}
          activeModelLabel={activeModelLabel}
          models={models}
          selectedModel={selectedModel}
          onModelChange={onModelChange}
          depth={depth}
          onDepthChange={onDepthChange}
          depthVisible={depthVisible}
          controlsDisabled={controlsDisabled}
          running={running}
          disabled={disabled}
          onSubmit={(payload) =>
            onSubmit?.({
              text: payload.text,
              attachments:
                payload.attachments as ReadonlyArray<CompleteAttachment>,
            })
          }
          onCancel={onCancel}
        />
      </div>
    </ThreadRoot>
  );
});
