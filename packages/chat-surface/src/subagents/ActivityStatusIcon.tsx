import type { ReactElement } from "react";

import { statusClassification } from "./subagentHelpers";

export function ActivityStatusIcon({
  status,
}: {
  status: string;
}): ReactElement {
  const { kind } = statusClassification(status);
  if (kind === "running") {
    return <span className="aui-activity-item__spinner" />;
  }
  if (kind === "error") {
    return <span className="aui-activity-item__mark">!</span>;
  }
  return <span className="aui-activity-item__mark">✓</span>;
}
