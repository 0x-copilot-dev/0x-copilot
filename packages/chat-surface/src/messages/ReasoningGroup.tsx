import type { ReactElement, ReactNode } from "react";

import { ThinkingIcon } from "../icons/ThinkingIcon";

export interface ReasoningGroupProps {
  readonly startIndex: number;
  readonly endIndex: number;
  readonly children?: ReactNode;
  /**
   * Synthesised by the host's `MessageParts` from the contained reasoning
   * parts' statuses. `running` while any child part is mid-stream;
   * `complete` once every child part has settled. Drives the "Thinking…" /
   * "Thought process" label flip and the streaming cursor.
   */
  readonly status?: "running" | "complete";
  /**
   * Synthesised by `MessageParts` as `max(updatedAtMs) - min(startedAtMs)`
   * rounded down to seconds. `0` when the contained parts lack timestamps.
   */
  readonly elapsedSeconds?: number;
}

/**
 * Thought-process accordion. Renders as a native `<details>` — collapsed by
 * default, keyboard-accessible (Enter/Space toggles, focus ring), and
 * announced as a disclosure by screen readers. The summary label flips
 * between "Thinking…" while reasoning is still streaming and "Thought
 * process" once the model has settled into text or tool calls. The
 * elapsed-time stamp on the right is synthesised by `MessageParts` from the
 * contained parts' first/latest event timestamps.
 *
 * `data-status` exposes the running/complete state to CSS so the body can
 * render the streaming cursor without a JS-side animation. CSS lives in THIS
 * package (`messages/markdown.css`), which both hosts import — it used to live
 * in the web app's stylesheet, so desktop rendered the group entirely
 * unstyled: base-size label, and the browser's own `<details>` triangle
 * because our marker reset never loaded.
 *
 * The chevron is authored here for the same reason the reset exists: once
 * `list-style: none` kills the native marker, a disclosure with no glyph of
 * its own has NO affordance at all. It is `aria-hidden` — `<details>` already
 * announces its own expanded state, so a second announcement would be noise.
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
        <svg
          className="aui-reasoning-group__chevron"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
          focusable="false"
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
      </summary>
      <div className="aui-reasoning-group__content">{children}</div>
    </details>
  );
}
