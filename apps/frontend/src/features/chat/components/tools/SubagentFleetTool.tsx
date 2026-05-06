// PR A2 — fleet bookend renderer.
//
// Mounts on `subagent_fleet_started` (and updates on the matching
// `subagent_fleet_finished`). Sibling `<SubagentTool>` parts whose
// `parent_fleet_id` arg matches this fleet's id continue to render
// individually below the card; this component carries the head + count
// + elapsed for the parallel batch.

import type { ReactElement } from "react";
import type { ToolCallMessagePartProps } from "../../runtime/types";
import { asRecord, stringValue } from "../../utils/jsonUtils";
import { SubagentFleetCard } from "../messages/SubagentFleetCard";

export function SubagentFleetTool(
  props: ToolCallMessagePartProps,
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
  const agentIds = readStringArray(data.agent_ids);
  const total = agentIds.length;
  const done = completed ? total : 0;
  const running = completed ? 0 : total;
  return (
    <SubagentFleetCard
      fleetId={fleetId}
      title={title}
      sub={sub}
      total={total}
      running={running}
      done={done}
      elapsed={elapsed}
    />
  );
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
