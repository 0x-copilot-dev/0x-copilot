import { Children, useState, type ReactElement, type ReactNode } from "react";

/**
 * PR 8.0.1 — Activity-card collapse rule.
 *
 * `assistant-ui` invokes `<ToolGroup>` once per run of consecutive
 * tool-call parts in the assistant message (a run ends when a text /
 * reasoning / non-tool part interrupts it). We count the children: if
 * there are < 4, render them inline (current behaviour); if there are
 * 4 or more, wrap them in a single collapsed `<ActivityCard>` with the
 * step list hidden by default. The card head shows the count; clicking
 * expands the body.
 *
 * Streaming-friendly:
 *   - The grouping decision is computed at render time from the parts
 *     list; replaying the same envelope sequence after SSE reconnect
 *     produces the same group (R2 in PR 8.0 §2.10).
 *   - A late `tool_call_completed` updates the underlying child in
 *     place; the group count is unchanged.
 *   - A `model_delta` between two tool-calls breaks the run (assistant-ui
 *     starts a new ToolGroup), so the next tool-call falls inline.
 */
const COLLAPSE_THRESHOLD = 4;

export function ToolGroup({
  children,
}: {
  startIndex: number;
  endIndex: number;
  children?: ReactNode;
}): ReactElement {
  const count = Children.count(children);
  const [open, setOpen] = useState(false);
  if (count < COLLAPSE_THRESHOLD) {
    return <>{children}</>;
  }
  return (
    <section
      className="aui-activity-group"
      data-open={open ? "true" : "false"}
      data-step-count={count}
    >
      <button
        type="button"
        className="aui-activity-group__head"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <span className="aui-activity-group__icon" aria-hidden="true">
          {open ? "▾" : "▸"}
        </span>
        <span className="aui-activity-group__summary">
          Reading {count} sources
        </span>
        <span className="aui-activity-group__hint">
          {open ? "Hide steps" : "Show steps"}
        </span>
      </button>
      {open ? <div className="aui-activity-group__body">{children}</div> : null}
    </section>
  );
}
