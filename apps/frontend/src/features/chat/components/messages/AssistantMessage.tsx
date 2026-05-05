import { MessagePrimitive, type ThreadMessageLike } from "@assistant-ui/react";
import type { ReactElement } from "react";
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
import { ToolFallback } from "../tools/ToolFallback";
import { ToolGroup } from "../tools/ToolGroup";
import { AssistantMessageFooter } from "./AssistantMessageFooter";
import { MessageSourcesStrip } from "./MessageSourcesStrip";

export function AssistantMessage({
  message,
  onMcpAuthConnect,
  onMcpAuthSkip,
  onOpenSources,
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
  /**
   * PR 3.5 / G9 — fires when a chip in the post-prose Sources strip is
   * clicked. The host (`ChatScreen`) routes it to
   * `paneState.openOn("sources", { focusCitationId })`. Optional so
   * non-pane mounts silently degrade.
   */
  onOpenSources?: (citationId: string) => void;
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
      {showStrip ? (
        <MessageSourcesStrip
          citations={sealedCitations}
          onSelect={(citation) => onOpenSources?.(citation.citation_id)}
        />
      ) : null}
      {showFooter ? <AssistantMessageFooter metrics={metrics} /> : null}
    </MessagePrimitive.Root>
  );
}

function readRunId(
  metadata: ThreadMessageLike["metadata"] | undefined,
): string | undefined {
  const value = metadata?.custom?.run_id;
  return typeof value === "string" && value.length > 0 ? value : undefined;
}
