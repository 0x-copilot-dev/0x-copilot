// PR 3.2.2 — in-thread subagent renderer.
//
// Pre PR 3.2.2 this used the generic <ActivityItem>, which made every
// subagent look identical to a tool call. Subagents are conceptually a
// nested autonomous run and deserve distinct chrome — title + task +
// finding + meta + expandable timeline of inner steps.
//
// Now delegates rendering to the shared <SubagentCard> primitive (also
// used by the workspace pane Agents tab in PR 3.2.1). The adapter
// `subagentCardFromArgs` shapes the in-thread `args` into the shared
// view-model contract; truncation + line-clamp happen there + in CSS.

import type { ToolCallMessagePartProps } from "../../runtime/types";
import type { ReactElement } from "react";
import { asRecord } from "../../utils/jsonUtils";
import { subagentActivityRecords } from "../../utils/activityDataBuilders";
import { SubagentCard } from "../subagents/SubagentCard";
import { subagentCardFromArgs } from "../subagents/subagentCardViewModel";

export function SubagentTool(props: ToolCallMessagePartProps): ReactElement {
  const args = asRecord(props.args);
  const view = subagentCardFromArgs(args, props.status.type, props.isError);
  const activities = subagentActivityRecords(args.activities);
  return <SubagentCard view={view} activities={activities} />;
}
