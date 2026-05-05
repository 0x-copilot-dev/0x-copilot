import {
  MessagePrimitive,
  SelectionToolbarPrimitive,
  ThreadPrimitive,
} from "@assistant-ui/react";
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
import { useAuth } from "../../../auth/AuthContext";
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
}): ReactElement {
  // Pull the first-token of the signed-in user's display_name for the
  // welcome greeting. SessionIdentity.display_name is optional today
  // (the auth contract doesn't ship the field through the HMAC bearer
  // yet — see api/authApi.ts); when it lands, this read picks it up
  // with no further wiring.
  const auth = useAuth();
  const greetingFirstName = firstNameFromDisplayName(
    auth.identity?.display_name ?? null,
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
          <ThreadWelcome firstName={greetingFirstName} />
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
              return (
                <MessagePrimitive.Root className="aui-system-message">
                  <MessagePrimitive.Parts components={{ Text: PlainText }} />
                </MessagePrimitive.Root>
              );
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
        />
      </div>
    </ThreadPrimitive.Root>
  );
}
