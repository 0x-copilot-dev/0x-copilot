import type { ReasoningGroupProps } from "../../runtime/types";
import type { ReactElement } from "react";
import { ThinkingIcon } from "../icons/ThinkingIcon";

export function ReasoningGroup({
  children,
}: ReasoningGroupProps): ReactElement {
  return (
    <details className="aui-reasoning-group" open>
      <summary>
        <ThinkingIcon />
        <span>Thinking</span>
      </summary>
      <div className="aui-reasoning-group__content">{children}</div>
    </details>
  );
}
