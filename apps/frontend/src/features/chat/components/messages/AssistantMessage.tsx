import { MessagePrimitive, type ThreadMessageLike } from "@assistant-ui/react";
import type { ReactElement } from "react";
import {
  isTerminalAssistantStatus,
  performanceMetricsFromMetadata,
} from "../../utils/activityDataBuilders";
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

export function AssistantMessage({
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
