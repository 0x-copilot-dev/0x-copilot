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

import { useMemo } from "react";
import { isToolCallPart } from "../../chatModel/recordHelpers";
import type { ChatItem } from "../../chatModel/types";
import {
  subagentActivityRecords,
  type SubagentActivityRecord,
} from "../../utils/activityDataBuilders";

export type SubagentActivitiesByTask = ReadonlyMap<
  string,
  readonly SubagentActivityRecord[]
>;

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
