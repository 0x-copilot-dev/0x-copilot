import type { CSSProperties, ReactElement } from "react";

import type { RunTerminalBeat } from "./runTerminalBeat";

/**
 * The run's verdict, rendered as the last beat of the chat stream.
 *
 * Deliberately NOT a canvas panel: it sits in the column the user is already
 * reading, so there is exactly one statement about how the run ended rather
 * than two panes disagreeing. It shows the goal it would re-send, because a
 * button that silently decides what to run is not an honest offer — and it
 * shows no button at all unless the runtime said a retry could change the
 * outcome.
 */
export function RunTerminalBeatCard(props: {
  readonly beat: RunTerminalBeat;
  readonly goal: string | null;
  readonly onStartNewRun?: () => void;
  readonly starting?: boolean;
}): ReactElement {
  const canRetry = props.beat.retryable && props.onStartNewRun !== undefined;
  return (
    <li
      aria-live="polite"
      data-testid="run-terminal-beat"
      data-code={props.beat.code ?? undefined}
      data-retryable={props.beat.retryable ? "true" : "false"}
      style={cardStyle}
    >
      <div style={headStyle}>
        <span aria-hidden="true" style={dotStyle} />
        <p style={titleStyle}>{props.beat.title}</p>
      </div>
      <p style={copyStyle}>{props.beat.copy}</p>
      {canRetry && props.goal !== null ? (
        <p style={goalStyle} title={props.goal}>
          {props.goal}
        </p>
      ) : null}
      {canRetry ? (
        <button
          type="button"
          onClick={props.onStartNewRun}
          disabled={props.starting === true}
          style={buttonStyle}
        >
          {props.starting === true
            ? "Starting…"
            : "Start a new run with this goal"}
        </button>
      ) : null}
    </li>
  );
}

const cardStyle: CSSProperties = {
  display: "grid",
  gap: "var(--space-sm, 8px)",
  listStyle: "none",
  padding: "var(--space-md, 12px)",
  border: "1px solid var(--color-border-strong, rgba(255,255,255,0.1))",
  borderRadius: "var(--radius-md, 8px)",
  background: "var(--color-surface, #111114)",
};
const headStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-sm, 8px)",
};
const dotStyle: CSSProperties = {
  width: 6,
  height: 6,
  borderRadius: "var(--radius-full, 999px)",
  background: "var(--color-danger, #f0764f)",
  flex: "0 0 auto",
};
const titleStyle: CSSProperties = {
  margin: 0,
  fontWeight: "var(--font-weight-semibold, 600)",
  color: "var(--color-text, #ececf1)",
};
const copyStyle: CSSProperties = {
  margin: 0,
  color: "var(--color-text-muted, #98989f)",
};
const goalStyle: CSSProperties = {
  margin: 0,
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-10-5, 10.5px)",
  color: "var(--color-text-muted, #98989f)",
  background: "var(--color-bg, #09090b)",
  border: "1px solid var(--color-border, rgba(255,255,255,0.06))",
  borderRadius: "var(--radius-sm, 6px)",
  padding: "var(--space-sm, 8px)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};
const buttonStyle: CSSProperties = {
  justifySelf: "start",
  border: 0,
  borderRadius: "var(--radius-sm, 6px)",
  padding: "var(--space-sm, 8px) var(--space-md, 12px)",
  background: "var(--color-accent, #5fb2ec)",
  color: "var(--color-accent-contrast, #08131d)",
  fontWeight: "var(--font-weight-semibold, 600)",
  cursor: "pointer",
};
