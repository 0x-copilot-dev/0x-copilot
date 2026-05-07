import type { ReactElement } from "react";
import { Message, MessageParts } from "../../runtime/components";
import type { ThreadMessageLike } from "../../runtime/types";
import {
  isTerminalAssistantStatus,
  performanceMetricsFromMetadata,
} from "../../utils/activityDataBuilders";
import { useRunCitations } from "../citations/citationsContext";
import { MarkdownText } from "../markdown/MarkdownText";
import { Reasoning } from "../markdown/Reasoning";
import { ReasoningGroup } from "../markdown/ReasoningGroup";
import { ApprovalTool } from "../tools/ApprovalTool";
import { ConnectorAuthTool } from "../tools/ConnectorAuthTool";
import { McpTool } from "../tools/McpTool";
import { ProgressTool } from "../tools/ProgressTool";
import { SubagentTool } from "../tools/SubagentTool";
import { SubagentFleetTool } from "../tools/SubagentFleetTool";
import { ToolFallback } from "../tools/ToolFallback";
import { ToolGroup } from "../tools/ToolGroup";
import { AssistantMessageFooter } from "./AssistantMessageFooter";
import { MessageSourcesStrip } from "./MessageSourcesStrip";

export function AssistantMessage({
  message,
  onMcpAuthConnect,
  onMcpAuthSkip,
  onMcpInstallCatalog,
  onMcpMuteCatalogSuggestion,
  onOpenSources,
  onResumeToolCall,
  onReload,
  onForkFromHere,
  onRequestUndo,
}: {
  message: ThreadMessageLike;
  onMcpAuthConnect: (payload: {
    approvalId: string;
    serverId: string;
  }) => Promise<void>;
  onMcpAuthSkip: (payload: {
    approvalId: string;
    serverId: string;
  }) => Promise<void>;
  /**
   * PR 4.4.7 Phase 2 (Slice C) — invoked when the discovery card was
   * emitted for a catalog-only suggestion (uninstalled). The host
   * (ChatScreen) branches on ``requiresPreRegisteredClient``: 1-click
   * vendors (false) get an inline install + auth + redirect chain;
   * pre-registered vendors (true) get a credentials form via the
   * McpOverlay. Optional so old call-sites keep working.
   */
  onMcpInstallCatalog?: (payload: {
    slug: string;
    requiresPreRegisteredClient: boolean;
    approvalId: string;
    serverId: string;
  }) => void;
  /** PR 4.4.7 Phase 2 — Skip on a catalog discovery card writes
   *  ``discoverable_connectors[slug] = false`` to the user's prefs
   *  so the agent never re-suggests this connector in future runs. */
  onMcpMuteCatalogSuggestion?: (payload: { slug: string }) => void;
  /**
   * PR 3.5 / G9 — fires when a chip in the post-prose Sources strip is
   * clicked. The host (`ChatScreen`) routes it to
   * `paneState.openOn("sources", { focusCitationId })`. Optional so
   * non-pane mounts silently degrade.
   */
  onOpenSources?: (citationId: string) => void;
  /**
   * Wired by the host (`ChatScreen`). Called by tool renderers when the
   * user resolves an interrupt — approval decision, MCP-auth choice,
   * ask-a-question answer. Forwarded into the runtime's resume pipeline.
   */
  onResumeToolCall?: (payload: unknown) => void;
  /**
   * Footer Reload button handler. Calls `runtime.reload(messageId)` via
   * the host. Optional — when omitted the Reload button is hidden so
   * read-only previews stay clean.
   */
  onReload?: () => void;
  /**
   * PR A3 — "Retry from here" handler. The host (`ChatScreen`) wires
   * this to `forkConversationFromMessage` and a navigation event so
   * the user lands in the freshly forked conversation.
   */
  onForkFromHere?: () => void;
  /**
   * PR 4.4.6.4 — invoked when the user clicks Undo on an approved +
   * reversible approval receipt. The host (`ChatScreen`) wires this
   * to ``requestApprovalUndo``. Optional — read-only / shared-chat
   * mounts pass ``undefined`` to hide the button.
   */
  onRequestUndo?: (
    approvalId: string,
  ) => Promise<{ undo_requested_at: string }>;
}): ReactElement {
  const metrics = performanceMetricsFromMetadata(message.metadata);
  const showFooter = isTerminalAssistantStatus(message.status);
  // PR 3.5 / G9 — runId is folded into metadata.custom by `chatItemsToThreadMessages`
  // (see chatModel/conversion.ts). When absent (optimistic / system messages),
  // useRunCitations returns the empty list and the strip renders nothing.
  const runId = readRunId(message.metadata);
  const sealedCitations = useRunCitations(runId, { sealedOnly: true });
  const showStrip = showFooter && sealedCitations.length > 0;
  return (
    <Message
      message={message}
      className="aui-message aui-message--assistant"
      onResumeToolCall={onResumeToolCall}
    >
      <div className="aui-message__body">
        <MessageParts
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
                run_subagent_fleet: SubagentFleetTool,
                run_progress: ProgressTool,
                approval_request: (props) => (
                  <ApprovalTool {...props} onRequestUndo={onRequestUndo} />
                ),
                mcp_auth_required: (props) => (
                  <ConnectorAuthTool
                    {...props}
                    onConnect={onMcpAuthConnect}
                    onSkip={onMcpAuthSkip}
                    onInstallCatalog={onMcpInstallCatalog}
                    onMuteCatalogSuggestion={onMcpMuteCatalogSuggestion}
                  />
                ),
              },
            },
          }}
        />
      </div>
      {showStrip ? (
        <MessageSourcesStrip
          citations={sealedCitations}
          onSelect={(citation) => onOpenSources?.(citation.citation_id)}
        />
      ) : null}
      {showFooter ? (
        <AssistantMessageFooter
          metrics={metrics}
          getText={() => textFromMessage(message)}
          onReload={onReload}
          onForkFromHere={onForkFromHere}
        />
      ) : null}
    </Message>
  );
}

function readRunId(
  metadata: ThreadMessageLike["metadata"] | undefined,
): string | undefined {
  const value = metadata?.custom?.run_id;
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function textFromMessage(message: ThreadMessageLike): string {
  const content = message.content;
  if (typeof content === "string") {
    return content;
  }
  if (!content) {
    return "";
  }
  const out: string[] = [];
  for (const part of content) {
    if (part.type === "text") {
      out.push(part.text);
    }
  }
  return out.join("\n");
}
