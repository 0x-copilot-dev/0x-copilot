import type { ToolCallMessagePartProps } from "../../runtime/types";
import { HarnessRow } from "@0x-copilot/design-system";
import type { ReactElement } from "react";
import { largeToolResultFromValue, stringValue } from "../../utils/jsonUtils";
import { inlineToolTitle, toolStatusLabel } from "../../utils/toolLabels";
import {
  shouldRenderFullToolCard,
  summarizeArgsText,
} from "../../utils/toolResultAnalysis";
import { activityParams } from "../../utils/activityDataBuilders";
import { ActivityCard } from "../activity/ActivityCard";
import { GeneratedPresentationCard } from "../activity/GeneratedPresentationCard";
import { presentationFromArgs } from "../activity/presentationHelpers";
import { toolDetailsContent } from "../details/toolDetailsContent";
import {
  safeMainResultSummary,
  summarizeToolValue,
} from "../results/summarize";

/**
 * Compact tool render path.
 *
 * - When the tool's args produced a `presentation` payload, render the
 *   generated presentation card (rich result with thumbnails / etc).
 * - When the result is "complex" (rich shape, error, requires action),
 *   render the full `<ActivityCard>`.
 * - Otherwise render a single inline `<HarnessRow>`:
 *
 *       ✓ tool_name (args) | → result
 *
 *   This matches the design doc's "compress tool calls" rule — raw tool
 *   name in mono, args truncated, dim result. The design's
 *   `<ActivityCard>` collapse for ≥ 4 consecutive harness rows is the
 *   responsibility of the parent renderer (PR follow-up); single rows
 *   render flush.
 */
export function ToolFallback({
  toolName,
  args,
  argsText,
  result,
  status,
  isError,
}: ToolCallMessagePartProps<Record<string, unknown>>): ReactElement {
  // The Atlas runtime's `MessageParts` spreads the raw content-part
  // object into tool components; that part-shape doesn't carry the
  // assistant-ui `status: { type }` envelope. Synthesize a safe
  // fallback so this component doesn't crash on `status.type`.
  const safeArgs = args ?? ({} as Record<string, unknown>);
  const safeStatusType =
    typeof status?.type === "string"
      ? status.type
      : result !== undefined
        ? "complete"
        : "running";
  const presentation = presentationFromArgs(safeArgs);
  const argsSummary = summarizeArgsText(argsText);
  const activitySummary = stringValue(safeArgs.summary) ?? argsSummary;
  const statusLabel = toolStatusLabel(safeStatusType, isError);
  const largeResult = largeToolResultFromValue(result);
  const title = inlineToolTitle(toolName, safeStatusType, isError, result);
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
        forceCard={shouldRenderFullToolCard(safeStatusType, isError, result)}
      />
    );
  }
  if (!shouldRenderFullToolCard(safeStatusType, isError, result)) {
    return (
      <HarnessRow
        status={harnessStatus(safeStatusType, isError)}
        tool={toolName}
        args={activitySummary ?? null}
        result={resultSummary ?? null}
      />
    );
  }
  return (
    <ActivityCard
      title={title}
      status={statusLabel}
      variant="tool"
      description={activitySummary}
      params={activityParams(argsText, safeArgs)}
      result={resultSummary}
      details={details}
    />
  );
}

function harnessStatus(
  status: string,
  isError: boolean | undefined,
): "running" | "done" | "error" {
  if (isError) {
    return "error";
  }
  if (status === "running" || status === "incomplete") {
    return "running";
  }
  return "done";
}
