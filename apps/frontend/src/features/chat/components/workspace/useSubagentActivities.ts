// PR 3.2.1 — pure selector over the conversation's chat tree.
//
// `upsertSubagentActivity` (chatModel/contentBuilders.ts:274) already
// nests `parent_task_id`-linked tool/reasoning events into the parent
// `run_subagent` tool part's `args.activities`. Both the in-thread
// `SubagentTool` and the workspace pane Agents tab read from there —
// single source of truth.
//
// This selector walks the chat items, finds every `run_subagent` tool
// part, and returns a `Map<task_id, SubagentActivityRecord[]>` so the
// pane can render each subagent's per-step timeline without a second
// fetch. Memoised over `items` reference; reference-stable across no-op
// renders.
//
// No new event variant. No new endpoint. No new persistence column.

import type { SubagentEntry } from "@enterprise-search/api-types";
import { useMemo } from "react";
import { isToolCallPart } from "../../chatModel/recordHelpers";
import { normaliseLifecycleStatus } from "../../chatModel/subagentStatus";
import type { ChatItem } from "../../chatModel/types";
import {
  subagentActivityRecords,
  type SubagentActivityRecord,
} from "../../utils/activityDataBuilders";
import { asRecord, stringValue } from "../../utils/jsonUtils";

export type SubagentActivitiesByTask = ReadonlyMap<
  string,
  readonly SubagentActivityRecord[]
>;

export interface SubagentHistoryGroup {
  id: string;
  label: string;
  timestamp: string | null;
  entries: readonly SubagentEntry[];
}

const EMPTY: SubagentActivitiesByTask = new Map();

/**
 * Walk the chat tree once and project `task_id → activities[]` for every
 * `run_subagent` tool part. Pure; testable without React.
 */
export function collectSubagentActivities(
  items: readonly ChatItem[],
): SubagentActivitiesByTask {
  if (items.length === 0) {
    return EMPTY;
  }
  let out: Map<string, readonly SubagentActivityRecord[]> | null = null;
  for (const item of items) {
    if (item.kind !== "message" || item.role !== "assistant") continue;
    for (const part of item.content) {
      if (!isToolCallPart(part)) continue;
      if (part.toolName !== "run_subagent") continue;
      const taskId = part.toolCallId;
      if (!taskId) continue;
      const activities = subagentActivityRecords(
        (part.args as Record<string, unknown> | undefined)?.activities,
      );
      if (activities.length === 0) {
        // Still register the task so callers can distinguish "subagent
        // exists with no inner steps" from "task_id not in tree" — both
        // map to the empty-list fallback in `SubagentActivityList`.
        if (out === null) out = new Map();
        out.set(taskId, []);
        continue;
      }
      if (out === null) out = new Map();
      out.set(taskId, activities);
    }
  }
  return out ?? EMPTY;
}

/**
 * `useMemo` wrapper for `collectSubagentActivities`. Reference-stable
 * across renders when `items` did not change (the chat tree uses
 * immutable updates).
 */
export function useSubagentActivities(
  items: readonly ChatItem[],
): SubagentActivitiesByTask {
  return useMemo(() => collectSubagentActivities(items), [items]);
}

export function collectSubagentHistory(
  items: readonly ChatItem[],
): readonly SubagentHistoryGroup[] {
  const groups = new Map<
    string,
    { id: string; timestamp: string | null; entries: SubagentEntry[] }
  >();
  for (const item of items) {
    if (item.kind !== "message" || item.role !== "assistant") continue;
    for (const part of item.content) {
      if (!isToolCallPart(part) || part.toolName !== "run_subagent") continue;
      const entry = entryFromSubagentPart(part, item.runId ?? item.id);
      if (entry === null) continue;
      const args = asRecord(part.args);
      const groupId =
        stringValue(args.parent_fleet_id) ?? entry.parent_run_id ?? item.id;
      const existing = groups.get(groupId);
      if (existing) {
        existing.entries.push(entry);
        existing.timestamp = earliestTimestamp(existing.timestamp, entry);
      } else {
        groups.set(groupId, {
          id: groupId,
          timestamp: entry.started_at ?? entry.completed_at,
          entries: [entry],
        });
      }
    }
  }
  return [...groups.values()]
    .map((group) => ({
      ...group,
      label: subagentGroupLabel(group.entries),
      entries: group.entries.sort(byEntryTime),
    }))
    .sort(byGroupRecency);
}

export function useSubagentHistory(
  items: readonly ChatItem[],
): readonly SubagentHistoryGroup[] {
  return useMemo(() => collectSubagentHistory(items), [items]);
}

function entryFromSubagentPart(
  part: Extract<ChatItem, { kind: "message" }>["content"][number],
  runId: string,
): SubagentEntry | null {
  if (!isToolCallPart(part)) return null;
  const args = asRecord(part.args);
  const taskId = part.toolCallId ?? stringValue(args.task_id);
  if (!taskId) return null;
  const status = statusFromArgs(args, part.isError);
  const startedAt = stringValue(args.started_at);
  return {
    task_id: taskId,
    parent_run_id: runId,
    subagent_name:
      stringValue(args.subagent_name) ??
      stringValue(args.name) ??
      stringValue(args.display_title) ??
      "subagent",
    status,
    display_title: stringValue(args.display_title),
    objective_summary:
      stringValue(args.objective_summary) ??
      stringValue(args.task_summary) ??
      stringValue(args.short_summary),
    started_at: startedAt,
    completed_at: status === "running" || status === "queued" ? null : null,
    duration_ms: null,
    result_summary: stringValue(args.summary),
    safe_error_code: part.isError ? "subagent_error" : null,
    safe_error_message: null,
    token_usage: null,
  };
}

function statusFromArgs(
  args: Record<string, unknown>,
  isError: boolean | undefined,
): SubagentEntry["status"] {
  return normaliseLifecycleStatus(stringValue(args.status), isError ?? false);
}

function earliestTimestamp(
  current: string | null,
  entry: SubagentEntry,
): string | null {
  const next = entry.started_at ?? entry.completed_at;
  if (current === null) return next;
  if (next === null) return current;
  return Date.parse(next) < Date.parse(current) ? next : current;
}

function subagentGroupLabel(entries: readonly SubagentEntry[]): string {
  const count = entries.length;
  return count === 1
    ? "1 subagent dispatched"
    : `${count} subagents dispatched`;
}

function byEntryTime(left: SubagentEntry, right: SubagentEntry): number {
  return entryTime(left) - entryTime(right);
}

function byGroupRecency(
  left: SubagentHistoryGroup,
  right: SubagentHistoryGroup,
): number {
  return timestampValue(right.timestamp) - timestampValue(left.timestamp);
}

function entryTime(entry: SubagentEntry): number {
  return timestampValue(entry.started_at ?? entry.completed_at);
}

function timestampValue(value: string | null): number {
  if (value === null) return -Number.MAX_SAFE_INTEGER;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? -Number.MAX_SAFE_INTEGER : parsed;
}
