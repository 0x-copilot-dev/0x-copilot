import type { ReactElement } from "react";

export function ActivityStatusIcon({
  status,
}: {
  status: string;
}): ReactElement {
  const normalized = status.toLowerCase();
  if (
    normalized === "running" ||
    normalized === "starting" ||
    normalized === "working" ||
    normalized === "still working" ||
    normalized === "waiting"
  ) {
    return <span className="aui-activity-item__spinner" />;
  }
  if (
    normalized === "error" ||
    normalized === "failed" ||
    normalized === "could not complete"
  ) {
    return <span className="aui-activity-item__mark">!</span>;
  }
  return <span className="aui-activity-item__mark">✓</span>;
}
