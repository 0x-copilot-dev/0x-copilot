import { classNames } from "@enterprise-search/design-system";
import type { ReactElement } from "react";

export type RunIndicator = {
  label: string;
  visible: boolean;
};

export function PlanningIndicator({
  label,
  visible,
}: RunIndicator): ReactElement {
  const words = label.split(" ");
  return (
    <div
      className="aui-planning-indicator"
      data-visible={visible ? "true" : "false"}
      role={visible ? "status" : undefined}
      aria-live={visible ? "polite" : undefined}
      aria-hidden={visible ? undefined : "true"}
      aria-label={label}
    >
      <span className="aui-planning-indicator__text" aria-hidden="true">
        {words.map((word, index) => (
          <span
            className={classNames(
              "aui-planning-indicator__word",
              `aui-planning-indicator__word--${index + 1}`,
            )}
            key={`${word}-${index}`}
          >
            {word}
          </span>
        ))}
      </span>
    </div>
  );
}
