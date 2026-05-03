import { classNames } from "@enterprise-search/design-system";
import type { ReactElement, ReactNode } from "react";
import { ActivityCollapsible } from "./ActivityCollapsible";
import { ActivityStatusIcon } from "./ActivityStatusIcon";
import type { ActivityVariant } from "./types";

export function ActivityItem({
  title,
  status,
  variant = "tool",
  description,
  details,
  detailsLabel = "Details",
  result,
  icon,
}: {
  title: string;
  status: string;
  variant?: ActivityVariant;
  description?: ReactNode;
  details?: ReactNode;
  detailsLabel?: string;
  result?: ReactNode;
  icon?: ReactNode;
}): ReactElement {
  const hasDetails = Boolean(details);
  return (
    <div
      className={classNames(
        "aui-activity-item",
        `aui-activity-item--${variant}`,
      )}
      data-status={status}
    >
      <div className="aui-activity-item__content">
        <span className="aui-activity-item__icon" aria-hidden="true">
          {icon ?? <ActivityStatusIcon status={status} />}
        </span>
        <div className="aui-activity-item__text">
          <div className="aui-activity-item__line">
            <span className="aui-activity-item__title">{title}</span>
            {description ? (
              <span className="aui-activity-item__description">
                {description}
              </span>
            ) : null}
          </div>
          {result ? (
            <div className="aui-activity-item__result">{result}</div>
          ) : null}
        </div>
      </div>
      <span className="aui-activity-item__status">{status}</span>
      {hasDetails ? (
        <ActivityCollapsible
          className="aui-activity-item__details"
          contentClassName="aui-activity-item__details-content"
          label={detailsLabel}
        >
          {details}
        </ActivityCollapsible>
      ) : null}
    </div>
  );
}
