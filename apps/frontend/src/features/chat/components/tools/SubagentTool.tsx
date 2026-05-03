import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import type { ReactElement } from "react";
import { asRecord, stringValue, truncateText } from "../../utils/jsonUtils";
import { formatAgentName } from "../../utils/toolLabels";
import { subagentActivityRecords } from "../../utils/activityDataBuilders";
import { ActivityItem } from "../activity/ActivityItem";
import { SubagentActivityList } from "./SubagentActivityList";
import {
  subagentCardTitle,
  subagentFallbackProgress,
  subagentInlineTitle,
  subagentStatusLabel,
  summarizeSubagentResult,
} from "./subagentText";
import { useElapsedSeconds } from "./useElapsedSeconds";

export function SubagentTool(props: ToolCallMessagePartProps): ReactElement {
  const data = asRecord(props.args);
  const subagentName =
    stringValue(data.subagent_name) ?? stringValue(data.name);
  const taskId = stringValue(data.task_id);
  const summary = stringValue(data.summary);
  const shortSummary = stringValue(data.short_summary);
  const taskSummary = shortSummary ?? stringValue(data.task_summary) ?? summary;
  const displayTitle = stringValue(data.display_title);
  const activities = subagentActivityRecords(data.activities);
  const dataStatus = stringValue(data.status);
  const normalizedStatus = dataStatus?.toLowerCase();
  const completed =
    props.status.type === "complete" ||
    ["completed", "succeeded", "success"].includes(normalizedStatus ?? "");
  const failed =
    props.isError === true ||
    normalizedStatus === "failed" ||
    normalizedStatus === "error";
  const cancelled = normalizedStatus === "cancelled";
  const terminal = completed || failed || cancelled;
  const elapsedSeconds = useElapsedSeconds(
    !terminal,
    stringValue(data.started_at),
  );
  const statusLabel = subagentStatusLabel(
    dataStatus ?? props.status.type,
    props.isError,
    elapsedSeconds,
  );
  const title = subagentCardTitle(displayTitle, taskSummary, completed);
  const fallbackProgress = subagentFallbackProgress(elapsedSeconds);
  const outputSummary = terminal
    ? summarizeSubagentResult(summary, taskSummary)
    : fallbackProgress;
  const details =
    import.meta.env.DEV && (taskId || subagentName) ? (
      <>
        {subagentName ? (
          <small>Agent: {formatAgentName(subagentName)}</small>
        ) : null}
        {taskId ? <small>Task ID: {taskId}</small> : null}
      </>
    ) : undefined;
  const hasActivityDetail = activities.length > 0;
  const activityDetails = hasActivityDetail ? (
    <SubagentActivityList
      activities={activities}
      emptyText={
        completed ? "No detailed activity was reported." : fallbackProgress
      }
    />
  ) : null;
  const resultDetails =
    terminal && summary ? (
      <>
        <small>Result</small>
        <pre>{truncateText(summary, 800)}</pre>
      </>
    ) : null;
  const subagentDetails =
    activityDetails || details || resultDetails ? (
      <>
        {activityDetails}
        {details}
        {resultDetails}
      </>
    ) : undefined;
  return (
    <ActivityItem
      title={subagentInlineTitle(completed, failed, cancelled)}
      status={statusLabel}
      variant="subagent"
      description={title}
      result={terminal ? undefined : outputSummary}
      details={subagentDetails}
    />
  );
}
