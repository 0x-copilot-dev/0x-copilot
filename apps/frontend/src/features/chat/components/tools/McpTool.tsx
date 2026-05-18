import type { ToolCallMessagePartProps } from "../../runtime/types";
import type { ReactElement } from "react";
import { largeToolResultFromValue, stringValue } from "../../utils/jsonUtils";
import {
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

// Title / summary come from the backend's projected ``display_title`` /
// ``summary`` (stashed into ``args`` by ``partFactories.toolPart``). The
// projector already unwraps the MCP dispatcher, so an event with
// ``payload.tool_name = "call_mcp_tool"`` and ``args.tool_name =
// "list_issues"`` arrives here as ``args.display_title = "Calling
// list_issues"``. Recomputing locally violates the project invariant in
// ``apps/frontend/CLAUDE.md`` and was the source of the "Action
// connector / Action connector" rows: at ``tool_call_started`` the inner
// args haven't streamed yet, so the local derivation produced the
// literal fallback. Trusting the projection means the row updates as
// soon as the next delta event arrives.
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
  const title = stringValue(args.display_title) ?? "Working on step";
  const description = stringValue(args.summary) ?? undefined;
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
