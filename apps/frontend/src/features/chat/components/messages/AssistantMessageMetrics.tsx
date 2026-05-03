import type { AssistantPerformanceMetrics } from "@enterprise-search/api-types";
import type { ReactElement } from "react";
import { formatMilliseconds } from "../../utils/jsonUtils";
import { metricRows } from "../../utils/activityDataBuilders";

export function AssistantMessageMetrics({
  metrics,
}: {
  metrics: AssistantPerformanceMetrics;
}): ReactElement {
  const rows = metricRows(metrics);
  return (
    <div
      className="aui-message-metrics"
      aria-label={rows.map((row) => `${row.label}: ${row.value}`).join(", ")}
    >
      <span className="aui-message-timing" tabIndex={0}>
        {formatMilliseconds(metrics.duration_ms)}
      </span>
      <div className="aui-message-metrics__tooltip" role="tooltip">
        {rows.map((row) => (
          <div className="aui-message-metrics__row" key={row.label}>
            <span>{row.label}</span>
            <strong>{row.value}</strong>
          </div>
        ))}
      </div>
    </div>
  );
}
