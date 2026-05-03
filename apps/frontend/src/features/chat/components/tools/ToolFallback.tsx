import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import type { ReactElement } from "react";
import { largeToolResultFromValue, stringValue } from "../../utils/jsonUtils";
import { inlineToolTitle, toolStatusLabel } from "../../utils/toolLabels";
import {
  shouldRenderFullToolCard,
  summarizeArgsText,
} from "../../utils/toolResultAnalysis";
import { activityParams } from "../../utils/activityDataBuilders";
import { ActivityCard } from "../activity/ActivityCard";
import { ActivityItem } from "../activity/ActivityItem";
import { GeneratedPresentationCard } from "../activity/GeneratedPresentationCard";
import { presentationFromArgs } from "../activity/presentationHelpers";
import { toolDetailsContent } from "../details/toolDetailsContent";
import {
  safeMainResultSummary,
  summarizeToolValue,
} from "../results/summarize";

export function ToolFallback({
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
