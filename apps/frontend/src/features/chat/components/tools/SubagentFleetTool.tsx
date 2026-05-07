// PR A2 — fleet bookend renderer.
// PR 3.2.4 — fleet card now nests its children as compact rows
// (`<FleetSubagentRow>`) instead of rendering full `<SubagentCard>`
// blocks both above and inside the card. The sibling `run_subagent`
// parts whose `args.parent_fleet_id` matches this fleet's `fleet_id` are
// passed as raw `ContentPart`s via `nestedChildren`; we project each one
// through the existing `subagentCardFromArgs` adapter so the row shares
// the standalone card's source of truth.

import type { ReactElement } from "react";
import type {
  MessagePartStatus,
  ToolCallMessagePartProps,
} from "../../runtime/types";
import { asRecord, stringValue } from "../../utils/jsonUtils";
import { FleetSubagentRow } from "../subagents/FleetSubagentRow";
import {
  subagentCardFromArgs,
  type SubagentCardStatus,
} from "../subagents/subagentCardViewModel";
import { SubagentFleetCard } from "../messages/SubagentFleetCard";
import { useSubagentFleetContext } from "../subagents/SubagentFleetContext";
import { scrollChatToEvent } from "../citations/scrollChatToCitation";
import type { SubagentActivityRecord } from "../../utils/activityDataBuilders";
import type { SubagentSnapshotMap } from "../../chatModel/subagentReducer";

type RawNestedChild = {
  readonly type?: string;
  readonly toolName?: string;
  readonly toolCallId?: string;
  readonly args?: Record<string, unknown>;
  readonly status?: MessagePartStatus;
  readonly isError?: boolean;
};

const NON_TERMINAL: ReadonlySet<SubagentCardStatus> = new Set([
  "queued",
  "running",
  "paused",
]);

/** PR 3.2.7 — extra props plumbed by `MessageParts.tsx` so the in-thread
 *  fleet card can render paused chrome from the workspace reducer's
 *  truth, expand inline timelines, and jump back to the gating approval
 *  card. All optional — pre-PR callsites still mount this tool without
 *  them and degrade to the running-only chrome. */
export interface SubagentFleetToolExtras {
  subagentsByTask?: SubagentSnapshotMap;
  activitiesByTask?: ReadonlyMap<string, readonly SubagentActivityRecord[]>;
  onJumpToApproval?: (sourceEventId: string) => void;
}

export function SubagentFleetTool(
  props: ToolCallMessagePartProps &
    SubagentFleetToolExtras & {
      nestedChildren?: readonly RawNestedChild[];
    },
): ReactElement | null {
  // PR 3.2.7 — read context fallbacks. Explicit props win when provided
  // (Storybook / tests / future direct callers). The context is the
  // standard production path: `ChatScreen` wraps the tree with
  // `<SubagentFleetProvider>` so any nested `SubagentFleetTool` can read
  // workspace state without re-threading every renderer in
  // `MessageParts.tsx`.
  const fleetContext = useSubagentFleetContext();
  const subagentsByTask = props.subagentsByTask ?? fleetContext.subagentsByTask;
  const activitiesByTask =
    props.activitiesByTask ?? fleetContext.activitiesByTask;
  const onJumpToApproval =
    props.onJumpToApproval ??
    fleetContext.onJumpToApproval ??
    scrollChatToEvent;
  // PR 4.4.7 — fleet card now exposes a "View in workspace →" link in
  // its footer. The handler comes from the chat-level provider so it
  // can call ``paneState.openOn("agents")``; falls back to undefined
  // when no provider is wired (the link simply isn't rendered).
  const onOpenWorkspace = fleetContext.onOpenWorkspace;
  const data = asRecord(props.args);
  const fleetId = stringValue(data.fleet_id);
  if (fleetId === null) {
    return null;
  }
  const title = stringValue(data.title) ?? "Subagents working in parallel";
  const sub = stringValue(data.sub);
  const elapsed = stringValue(data.elapsed);
  const completed = data.completed === true;
  const declaredAgentIds = readStringArray(data.agent_ids);

  const children = props.nestedChildren ?? [];
  const childCount = children.length;
  // PR 3.2.4 AC-7 — head counts derive from the actual children we hold,
  // not the static `agent_ids.length` flag from the fleet event. The
  // worker's flag is advisory; live state is authoritative.
  const total = childCount > 0 ? childCount : declaredAgentIds.length;
  let running = 0;
  let done = 0;
  const rows: ReactElement[] = [];
  for (const child of children) {
    if (!isToolCall(child) || child.toolName !== "run_subagent") continue;
    const childArgs = asRecord(child.args);
    const childTaskId = stringValue(childArgs.task_id);
    // PR 3.2.7 — overlay the workspace reducer's pause state onto the
    // args-derived view model. The args accumulator only carries the
    // running/completed status; `subagent_paused` events update the
    // SubagentEntry, not the tool part. Reading the entry by task_id
    // gives the row the same paused chrome the pane card renders.
    const entry =
      childTaskId !== null ? subagentsByTask?.get(childTaskId) : undefined;
    const pauseOverlay =
      entry && entry.status === "paused"
        ? {
            statusOverride: "paused" as const,
            pauseReason: entry.pause_reason ?? null,
            pauseSourceEventId: entry.pause_source_event_id ?? null,
          }
        : undefined;
    const view = subagentCardFromArgs(
      childArgs,
      child.status?.type,
      child.isError,
      pauseOverlay,
    );
    if (NON_TERMINAL.has(view.status)) {
      running += 1;
    } else {
      done += 1;
    }
    const progress = numberValue(childArgs.progress);
    const activities =
      childTaskId !== null ? activitiesByTask?.get(childTaskId) : undefined;
    rows.push(
      <FleetSubagentRow
        key={child.toolCallId ?? view.taskId ?? `row-${rows.length}`}
        view={view}
        progress={progress}
        activities={activities}
        onJumpToApproval={onJumpToApproval}
      />,
    );
  }
  if (childCount === 0 && completed) {
    done = total;
  }
  return (
    <SubagentFleetCard
      fleetId={fleetId}
      title={title}
      sub={sub}
      total={total}
      running={running}
      done={done}
      elapsed={elapsed}
      onOpenWorkspace={onOpenWorkspace}
    >
      {rows}
    </SubagentFleetCard>
  );
}

function isToolCall(child: RawNestedChild): boolean {
  return child.type === "tool-call";
}

function readStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const out: string[] = [];
  for (const item of value) {
    if (typeof item === "string" && item.trim().length > 0) {
      out.push(item);
    }
  }
  return out;
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
