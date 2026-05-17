import { ThinkingIcon } from "@enterprise-search/chat-surface";
import type { ReactElement } from "react";

import type { ReasoningGroupProps } from "../../runtime/types";

/**
 * Atlas thought-process accordion. Renders as a native `<details>` —
 * collapsed by default, keyboard-accessible (Enter/Space toggles, focus
 * ring), and announced as a disclosure by screen readers. The summary
 * label flips between "Thinking…" while reasoning is still streaming
 * and "Thought process" once the model has settled into text or tool
 * calls. The elapsed-time stamp on the right is synthesised by
 * `MessageParts` from the contained parts' first/latest event
 * timestamps.
 *
 * `data-status` exposes the running/complete state to CSS so the body
 * can render the streaming cursor without a JS-side animation. CSS
 * lives in `apps/frontend/src/styles.css` under `.aui-reasoning-group`.
 */
export function ReasoningGroup({
  children,
  status = "complete",
  elapsedSeconds = 0,
}: ReasoningGroupProps): ReactElement {
  return (
    <details className="aui-reasoning-group" data-status={status}>
      <summary>
        <span className="aui-reasoning-group__icon" aria-hidden="true">
          <ThinkingIcon />
        </span>
        <span className="aui-reasoning-group__label">
          {status === "running" ? "Thinking…" : "Thought process"}
        </span>
        <span className="aui-reasoning-group__time">
          {elapsedSeconds > 0 ? `${elapsedSeconds}s` : ""}
        </span>
      </summary>
      <div className="aui-reasoning-group__content">{children}</div>
    </details>
  );
}
