// PR 3.2.2 — adapter that shapes both upstream subagent data sources
// (in-thread `args` from the `run_subagent` tool part, and workspace-pane
// `SubagentEntry` from PR 1.5) into a single view model the shared
// `<SubagentCard>` component renders. One adapter, two builders, one
// component — DRY by construction.
//
// Truncation happens here (defense in depth on top of CSS line-clamp).
// Markdown code fences are stripped from the `task` and `finding` text
// summaries so a sidebar card never carries fenced code; the disclosure
// body keeps the raw text (truncated at char level) for users who want
// the rest.

import type {
  SubagentEntry,
  SubagentLifecycleStatus,
} from "@enterprise-search/api-types";
import { asRecord, stringValue, truncateText } from "../../utils/jsonUtils";
import { formatAgentName } from "../../utils/toolLabels";

export type SubagentCardStatus = SubagentLifecycleStatus;

export type SubagentPauseReason = "approval" | "mcp_auth" | "ask_a_question";

export interface SubagentCardViewModel {
  /** task_id — used for data-testid + aria. May be null in the thread
   *  callsite if the run_subagent part hasn't been seeded with one. */
  taskId: string | null;
  /** Display name (e.g. "Doc reader" or "research"). */
  name: string;
  /** Lifecycle status (normalised). */
  status: SubagentCardStatus;
  /** Whether the lifecycle is terminal (anything except queued/running). */
  terminal: boolean;
  /** What the subagent was asked to do (task line in the card). */
  task: string | null;
  /** What the subagent reports (finding line — terminal subagents only). */
  finding: string | null;
  /** The full result text used by the disclosure body when activities is
   *  empty; null when nothing meaningful to show. */
  fullResult: string | null;
  /** ISO timestamp; null if never started. */
  startedAt: string | null;
  /** ISO timestamp; null while running. */
  completedAt: string | null;
  /** Server-projected duration. */
  durationMs: number | null;
  /** Drives danger badge tone in the card. */
  isError: boolean;
  /** PR 3.2.7 — set when status === "paused". Drives the row/card paused
   *  copy. Comes from the most recent `subagent_paused` payload merged
   *  into the entry by `subagentReducer`. */
  pauseReason?: SubagentPauseReason;
  /** PR 3.2.7 — event_id of the gating interrupt event on the same
   *  thread. Used by the row/card "Review approval →" link to
   *  anchor-scroll. */
  pauseSourceEventId?: string;
}

const TASK_MAX = 160;
const FINDING_MAX = 280;
const FULL_RESULT_MAX = 600;

/** Strip markdown code fences and collapse whitespace so a one-line
 *  summary derived from a result containing code never shows `\`\`\`lang`
 *  markers. The disclosure body keeps the raw text. */
function flattenForSummary(input: string): string {
  return input
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`[^`]*`/g, " ")
    .replace(/[\r\n]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function deriveTaskText(...candidates: Array<string | null>): string | null {
  for (const candidate of candidates) {
    if (!candidate) continue;
    const flat = flattenForSummary(candidate);
    if (flat.length === 0) continue;
    return truncateText(flat, TASK_MAX);
  }
  return null;
}

function deriveFindingText(...candidates: Array<string | null>): string | null {
  for (const candidate of candidates) {
    if (!candidate) continue;
    const flat = flattenForSummary(candidate);
    if (flat.length === 0) continue;
    return truncateText(flat, FINDING_MAX);
  }
  return null;
}

function deriveFullResult(...candidates: Array<string | null>): string | null {
  for (const candidate of candidates) {
    if (!candidate) continue;
    const trimmed = candidate.trim();
    if (trimmed.length === 0) continue;
    return truncateText(trimmed, FULL_RESULT_MAX);
  }
  return null;
}

const TERMINAL_STATUSES: ReadonlySet<SubagentCardStatus> = new Set([
  "completed",
  "cancelled",
  "failed",
  "timed_out",
]);

function isTerminalStatus(status: SubagentCardStatus): boolean {
  return TERMINAL_STATUSES.has(status);
}

function normaliseStatus(
  raw: string | null,
  isError: boolean,
): SubagentCardStatus {
  const lc = raw?.toLowerCase() ?? "";
  if (isError || lc === "failed" || lc === "error") return "failed";
  if (lc === "cancelled" || lc === "canceled") return "cancelled";
  if (lc === "timed_out" || lc === "timeout") return "timed_out";
  if (lc === "completed" || lc === "succeeded" || lc === "success") {
    return "completed";
  }
  if (lc === "queued") return "queued";
  return "running";
}

/** Build a view model from the in-thread `run_subagent` tool part's
 *  args. The reducer populates these fields incrementally as
 *  `subagent_started/progress/completed` events arrive. */
export function subagentCardFromArgs(
  args: Record<string, unknown>,
  partStatusType: string | undefined,
  isError: boolean | undefined,
  pauseOverlay?: {
    pauseReason?: SubagentPauseReason | null;
    pauseSourceEventId?: string | null;
    statusOverride?: SubagentCardStatus | null;
  },
): SubagentCardViewModel {
  const data = asRecord(args);
  const subagentName =
    stringValue(data.subagent_name) ?? stringValue(data.name);
  const taskId = stringValue(data.task_id);
  const summary = stringValue(data.summary);
  const shortSummary = stringValue(data.short_summary);
  const taskSummary = stringValue(data.task_summary);
  const displayTitle = stringValue(data.display_title);
  const objectiveSummary = stringValue(data.objective_summary);
  const startedAt = stringValue(data.started_at);
  const completedAt = stringValue(data.completed_at);
  const durationMs = numberValue(data.duration_ms);
  const dataStatus = stringValue(data.status);
  const errorFlag = Boolean(isError) || data.is_error === true;
  // PR 3.2.7 — the in-thread `run_subagent` tool part doesn't merge
  // pause/resume payloads into args; the workspace `SubagentEntry`
  // (kept in sync by `subagentReducer`) does. The fleet tool reads the
  // entry's `status` / `pause_reason` / `pause_source_event_id` and
  // hands them in here as an overlay so the in-thread row renders the
  // same paused chrome the pane card does. Without an overlay we fall
  // back to args-derived status (preserving pre-PR behavior).
  const baseStatus = normaliseStatus(
    dataStatus ?? partStatusType ?? null,
    errorFlag,
  );
  const status =
    pauseOverlay?.statusOverride && !isTerminalStatus(baseStatus)
      ? pauseOverlay.statusOverride
      : baseStatus;
  const terminal = isTerminalStatus(status);
  return {
    taskId,
    name: subagentName ? formatAgentName(subagentName) : "Subagent",
    status,
    terminal,
    // Prefer the most descriptive task text first, fall back to the
    // shorter labels. `display_title` is the short label (e.g. "Doc
    // reader") and is already used as the card name; using it as the
    // task line would just repeat the name.
    task: deriveTaskText(
      shortSummary,
      taskSummary,
      objectiveSummary,
      displayTitle,
    ),
    finding: terminal ? deriveFindingText(summary) : null,
    fullResult: terminal ? deriveFullResult(summary) : null,
    startedAt,
    completedAt,
    durationMs: durationMs ?? durationFromStarted(startedAt, completedAt),
    isError: errorFlag,
    pauseReason:
      status === "paused" && pauseOverlay?.pauseReason
        ? pauseOverlay.pauseReason
        : undefined,
    pauseSourceEventId:
      status === "paused" && pauseOverlay?.pauseSourceEventId
        ? pauseOverlay.pauseSourceEventId
        : undefined,
  };
}

/** Build a view model from the workspace pane's `SubagentEntry` (PR 1.5).
 *  Server-projected; richer than the thread args (carries
 *  `objective_summary`, `result_summary`, `duration_ms`). */
export function subagentCardFromEntry(
  entry: SubagentEntry,
): SubagentCardViewModel {
  const errorFlag = entry.status === "failed" || entry.safe_error_code !== null;
  const status = normaliseStatus(entry.status, errorFlag);
  const terminal = isTerminalStatus(status);
  return {
    taskId: entry.task_id,
    name: formatAgentName(entry.subagent_name),
    status,
    terminal,
    // Prefer the longer `objective_summary` over the short
    // `display_title` for the task line — `display_title` is the role
    // label and would just repeat the card name.
    task: deriveTaskText(entry.objective_summary, entry.display_title),
    finding: terminal ? deriveFindingText(entry.result_summary) : null,
    fullResult: terminal ? deriveFullResult(entry.result_summary) : null,
    startedAt: entry.started_at,
    completedAt: entry.completed_at,
    durationMs: entry.duration_ms,
    isError: errorFlag,
    pauseReason:
      status === "paused" && entry.pause_reason
        ? (entry.pause_reason as SubagentPauseReason)
        : undefined,
    pauseSourceEventId:
      status === "paused" && entry.pause_source_event_id
        ? entry.pause_source_event_id
        : undefined,
  };
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function durationFromStarted(
  startedAt: string | null,
  completedAt: string | null,
): number | null {
  if (!startedAt || !completedAt) return null;
  const started = Date.parse(startedAt);
  const completed = Date.parse(completedAt);
  if (Number.isNaN(started) || Number.isNaN(completed)) return null;
  return Math.max(0, completed - started);
}
