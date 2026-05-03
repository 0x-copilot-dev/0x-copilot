import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import type { ReactElement } from "react";
import { largeToolResultFromValue, stringValue } from "../../utils/jsonUtils";
import {
  inlineMcpToolTitle,
  mcpToolSummary,
  safeConnectorDisplayName,
  toolStatusLabel,
} from "../../utils/toolLabels";
import { shouldRenderFullMcpCard } from "../../utils/toolResultAnalysis";
import { mcpActivityParams } from "../../utils/activityDataBuilders";
import { ActivityCard } from "../activity/ActivityCard";
import { ActivityItem } from "../activity/ActivityItem";
import { GeneratedPresentationCard } from "../activity/GeneratedPresentationCard";
import { presentationFromArgs } from "../activity/presentationHelpers";
import { toolDetailsContent } from "../details/toolDetailsContent";
import {
  safeMainResultSummary,
  summarizeMcpResult,
} from "../results/summarize";

export function McpTool({
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
