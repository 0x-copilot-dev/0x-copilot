import { classNames } from "@0x-copilot/design-system";
import type { ReactElement } from "react";
import type { ActivityParam } from "./types";

export function ActivityParams({
  params,
}: {
  params: ActivityParam[];
}): ReactElement {
  return (
    <dl className="aui-activity-card__params">
      {params.map((param) => (
        <div
          className={classNames(
            "aui-activity-card__param",
            param.block ? "aui-activity-card__param--block" : undefined,
          )}
          key={param.label}
        >
          <dt>{param.label}</dt>
          <dd>{param.value}</dd>
        </div>
      ))}
    </dl>
  );
}
