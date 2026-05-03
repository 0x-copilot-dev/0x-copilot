import type { ThreadMessageLike } from "@assistant-ui/react";
import type { AssistantPerformanceMetrics } from "@enterprise-search/api-types";
import { isAssistantPerformanceMetrics } from "@enterprise-search/api-types";
import {
  asRecord,
  formatArgLabel,
  formatInlineValue,
  formatMilliseconds,
  formatNumber,
  hasVisibleValue,
  parseJsonValue,
  parseToolArgs,
  stringValue,
  visibleToolArgEntries,
} from "./jsonUtils";
import { isComplexToolValue } from "./toolResultAnalysis";
import {
  safeConnectorDisplayName,
  safeToolActionLabel,
  toolDisplayName,
} from "./toolLabels";
import type { ReactNode } from "react";

export type ActivityParam = {
  label: string;
  value: ReactNode;
  block?: boolean;
};

export type SubagentActivityRecord = {
  id: string;
  kind: string;
  title: string;
  status: string;
  summary: string | null;
  inputSummary: string | null;
  result: string | null;
  isError: boolean;
};

export function performanceMetricsFromMetadata(
  metadata: ThreadMessageLike["metadata"] | undefined,
): AssistantPerformanceMetrics | null {
  const metrics = metadata?.custom?.performance_metrics;
  return isAssistantPerformanceMetrics(metrics) ? metrics : null;
}

export function isTerminalAssistantStatus(
  status: ThreadMessageLike["status"] | undefined,
): boolean {
  return status?.type === "complete" || status?.type === "incomplete";
}

export function metricRows(
  metrics: AssistantPerformanceMetrics,
): Array<{ label: string; value: string }> {
  const rows: Array<{ label: string; value: string }> = [];
  if (metrics.first_chunk_ms !== undefined) {
    rows.push({
      label: "First token",
      value: formatMilliseconds(metrics.first_chunk_ms),
    });
  }
  rows.push({
    label: "Total",
    value: formatMilliseconds(metrics.duration_ms),
  });
  if (metrics.usage?.output_per_second !== undefined) {
    rows.push({
      label: "Speed",
      value: `${formatNumber(metrics.usage.output_per_second)} tok/s`,
    });
  }
  return rows;
}

export function activityParams(
  argsText: string | undefined,
  args: Record<string, unknown>,
): ActivityParam[] {
  const parsed = argsText ? parseToolArgs(argsText) : null;
  return visibleToolArgEntries(parsed ?? args)
    .slice(0, 5)
    .map(([key, value]) => ({
      label: formatArgLabel(key),
      value: formatInlineValue(value),
      block: false,
    }));
}

export function mcpActivityParams(
  serverName: string | null,
  toolName: string | null,
  args: unknown,
): ActivityParam[] {
  const params: ActivityParam[] = [];
  if (serverName) {
    params.push({ label: "App", value: safeConnectorDisplayName(serverName) });
  }
  if (toolName) {
    params.push({ label: "Action", value: safeToolActionLabel(toolName) });
  }
  if (args !== undefined && hasVisibleValue(args)) {
    const displayArgs = parseJsonValue(args) ?? args;
    if (!isComplexToolValue(displayArgs)) {
      params.push({
        label: "Input",
        value: formatInlineValue(displayArgs),
        block: false,
      });
    }
  }
  return params;
}

export function subagentActivityRecord(
  value: unknown,
): SubagentActivityRecord | null {
  const record = asRecord(value);
  const id = stringValue(record.id);
  if (!id) {
    return null;
  }
  return {
    id,
    kind: stringValue(record.kind) ?? "activity",
    title: stringValue(record.title) ?? "Activity",
    status: stringValue(record.status) ?? "running",
    summary: stringValue(record.summary),
    inputSummary: stringValue(record.input_summary),
    result: stringValue(record.result),
    isError: record.is_error === true,
  };
}

function isSubagentActivityRecord(
  value: SubagentActivityRecord | null,
): value is SubagentActivityRecord {
  return value !== null;
}

export function subagentActivityRecords(
  value: unknown,
): SubagentActivityRecord[] {
  return Array.isArray(value)
    ? value.map(subagentActivityRecord).filter(isSubagentActivityRecord)
    : [];
}

export function hasImportantSubagentActivity(
  activities: SubagentActivityRecord[],
): boolean {
  return activities.some(
    (activity) =>
      activity.isError ||
      !["complete", "completed"].includes(activity.status.toLowerCase()),
  );
}

export function activityTitle(activity: SubagentActivityRecord): string {
  if (activity.kind === "tool") {
    return activity.isError
      ? `Could not run ${toolDisplayName(activity.title)}`
      : toolDisplayName(activity.title);
  }
  return activity.title;
}
