import { truncateText } from "../../utils/jsonUtils";

export function subagentCardTitle(
  displayTitle: string | null,
  taskSummary: string | null,
  completed: boolean,
): string {
  const title = displayTitle ?? taskSummary;
  if (title) {
    return truncateText(title, 96);
  }
  return completed ? "Background task finished" : "Working in the background";
}

export function subagentInlineTitle(
  completed: boolean,
  failed: boolean,
  cancelled: boolean,
): string {
  if (failed) {
    return "Subagent failed";
  }
  if (cancelled) {
    return "Subagent cancelled";
  }
  return completed ? "Subagent finished" : "Subagent working";
}

export function subagentStatusLabel(
  status: string,
  isError: boolean | undefined,
  elapsedSeconds: number,
): string {
  const normalized = status.toLowerCase();
  if (isError || normalized === "failed" || normalized === "error") {
    return "could not complete";
  }
  if (normalized === "cancelled") {
    return "cancelled";
  }
  if (
    normalized === "complete" ||
    normalized === "completed" ||
    normalized === "succeeded" ||
    normalized === "success"
  ) {
    return "done";
  }
  if (elapsedSeconds >= 35) {
    return "still working";
  }
  if (normalized === "queued" || normalized === "started") {
    return "starting";
  }
  return "working";
}

export function subagentFallbackProgress(elapsedSeconds: number): string {
  if (elapsedSeconds >= 35) {
    return "Still working. Larger tasks can take about a minute.";
  }
  if (elapsedSeconds >= 15) {
    return "Working through the details...";
  }
  if (elapsedSeconds >= 5) {
    return "Gathering context...";
  }
  return "Starting task...";
}

export function summarizeSubagentResult(
  summary: string | null,
  taskSummary: string | null,
): string | undefined {
  if (!summary || summary === taskSummary) {
    return undefined;
  }
  return truncateText(summary, 140);
}
