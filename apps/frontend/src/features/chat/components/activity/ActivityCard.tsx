import { Badge, Card, classNames } from "@enterprise-search/design-system";
import type { ReactElement, ReactNode } from "react";
import type { ActivityParam } from "../../utils/activityDataBuilders";
import { badgeToneForStatus } from "../../utils/toolLabels";
import { ActivityDetails } from "./ActivityDetails";
import { ActivityParams } from "./ActivityParams";
import type { ActivityVariant } from "./types";

export function ActivityCard({
  title,
  status,
  variant = "tool",
  description,
  params = [],
  result,
  details,
  detailsLabel = "Tool details",
  children,
  className,
}: {
  title: string;
  status: string;
  variant?: ActivityVariant;
  description?: ReactNode;
  params?: ActivityParam[];
  result?: ReactNode;
  details?: ReactNode;
  detailsLabel?: string;
  children?: ReactNode;
  className?: string;
}): ReactElement {
  return (
    <Card
      className={classNames(
        "aui-tool-card",
        "aui-activity-card",
        `aui-activity-card--${variant}`,
        className,
      )}
      data-status={status}
    >
      <header className="aui-activity-card__header">
        <span className="aui-activity-card__status-dot" aria-hidden="true" />
        <div className="aui-activity-card__heading">
          <span className="aui-activity-card__title">{title}</span>
          {description ? (
            <p className="aui-activity-card__description">{description}</p>
          ) : null}
        </div>
        <Badge tone={badgeToneForStatus(status)}>{status}</Badge>
      </header>
      {params.length > 0 ? <ActivityParams params={params} /> : null}
      {result ? (
        <div className="aui-activity-card__result">{result}</div>
      ) : null}
      {children}
      {details ? (
        <ActivityDetails label={detailsLabel}>{details}</ActivityDetails>
      ) : null}
    </Card>
  );
}
