import type { ReactElement } from "react";
import { truncateText } from "../../utils/jsonUtils";
import {
  activityTitle,
  type SubagentActivityRecord,
} from "../../utils/activityDataBuilders";

export function SubagentActivityList({
  activities,
  emptyText = "No detailed activity was reported.",
}: {
  activities: SubagentActivityRecord[];
  emptyText?: string;
}): ReactElement {
  if (activities.length === 0) {
    return <p className="aui-tool-card__empty">{emptyText}</p>;
  }
  return (
    <div className="aui-tool-card__timeline">
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
