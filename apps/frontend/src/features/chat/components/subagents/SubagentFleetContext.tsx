// PR 3.2.7 — thin React context that lets `<SubagentFleetTool>` (deep
// inside the message-parts render tree) read the workspace pane's
// `subagents` snapshot, the screen-level `activitiesByTask` projection,
// and the chat-level `onJumpToApproval` handler — none of which it
// previously had a path to without re-threading every renderer in
// `MessageParts.tsx`.
//
// The context is intentionally optional: every value is undefined-safe,
// so old tests / Storybook / standalone renders keep working without
// providing it. The provider lives in `ChatScreen.tsx` next to where
// the data is already hoisted.

import { createContext, useContext, type ReactNode } from "react";

import type { SubagentSnapshotMap } from "../../chatModel/subagentReducer";
import type { SubagentActivityRecord } from "../../utils/activityDataBuilders";

export type SubagentActivitiesByTaskMap = ReadonlyMap<
  string,
  readonly SubagentActivityRecord[]
>;

export interface SubagentFleetContextValue {
  /** Workspace reducer's `task_id → SubagentEntry` snapshot. The fleet
   *  tool reads this to overlay paused state / pauseReason /
   *  pauseSourceEventId on rows when the corresponding `SubagentEntry`
   *  is paused. */
  subagentsByTask?: SubagentSnapshotMap;
  /** Screen-hoisted `task_id → activities[]` projection. The fleet
   *  row's inline-timeline disclosure reads this to render the same
   *  per-subagent activity list the standalone `<SubagentCard>`
   *  shows. */
  activitiesByTask?: SubagentActivitiesByTaskMap;
  /** Handler invoked by the row's "Review approval →" link. Defaults
   *  to `scrollChatToEvent` if absent. */
  onJumpToApproval?: (sourceEventId: string) => void;
  /** PR 4.4.7 — opens the workspace pane on the Agents tab. Used by the
   *  fleet card's "View in workspace →" footer link. Optional so old
   *  tests / Storybook still render without the link. */
  onOpenWorkspace?: () => void;
}

const SubagentFleetContext = createContext<SubagentFleetContextValue>({});

export function SubagentFleetProvider({
  value,
  children,
}: {
  value: SubagentFleetContextValue;
  children: ReactNode;
}) {
  return (
    <SubagentFleetContext.Provider value={value}>
      {children}
    </SubagentFleetContext.Provider>
  );
}

export function useSubagentFleetContext(): SubagentFleetContextValue {
  return useContext(SubagentFleetContext);
}
