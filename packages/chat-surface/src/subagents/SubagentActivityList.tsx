import type { ReactElement } from "react";

import {
  activityTitle,
  truncateText,
  type SubagentActivityRecord,
} from "./subagentHelpers";

export function SubagentActivityList({
  activities,
  emptyText = "No detailed activity was reported.",
  className = "aui-tool-card__timeline",
}: {
  activities: SubagentActivityRecord[];
  emptyText?: string;
  /** Override the timeline container class. Defaults to the in-thread
   *  `aui-tool-card__timeline` styling; the workspace pane composes a
   *  pane-narrow variant on top via PR 3.2.1. */
  className?: string;
}): ReactElement {
  if (activities.length === 0) {
    return <p className="aui-tool-card__empty">{emptyText}</p>;
  }
  return (
    <div className={className}>
      {activities.map((activity) => (
        <div className="aui-tool-card__timeline-item" key={activity.id}>
          <div>
            <span className="aui-tool-card__timeline-title">
              {activityTitle(activity)}
            </span>
            {activity.summary ? (
              <p>{truncateText(activity.summary, 160)}</p>
            ) : null}
            {!activity.summary && activity.inputSummary ? (
              <p>{truncateText(activity.inputSummary, 160)}</p>
            ) : null}
            {activity.result ? (
              <p>{truncateText(activity.result, 160)}</p>
            ) : null}
          </div>
          <span>{activity.status}</span>
        </div>
      ))}
    </div>
  );
}
