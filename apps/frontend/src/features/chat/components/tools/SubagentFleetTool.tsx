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
]);

export function SubagentFleetTool(
  props: ToolCallMessagePartProps & {
    nestedChildren?: readonly RawNestedChild[];
  },
): ReactElement | null {
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
    const view = subagentCardFromArgs(
      asRecord(child.args),
      child.status?.type,
      child.isError,
    );
    if (NON_TERMINAL.has(view.status)) {
      running += 1;
    } else {
      done += 1;
    }
    const progress = numberValue(asRecord(child.args).progress);
    rows.push(
      <FleetSubagentRow
        key={child.toolCallId ?? view.taskId ?? `row-${rows.length}`}
        view={view}
        progress={progress}
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
