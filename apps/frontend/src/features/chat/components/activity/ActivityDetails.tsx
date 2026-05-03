import type { ReactElement, ReactNode } from "react";
import { ActivityCollapsible } from "./ActivityCollapsible";

export function ActivityDetails({
  children,
  label = "Tool details",
}: {
  children: ReactNode;
  label?: string;
}): ReactElement {
  return (
    <ActivityCollapsible
      className="aui-activity-card__details"
      contentClassName="aui-activity-card__details-content"
      label={label}
    >
      {children}
    </ActivityCollapsible>
  );
}
