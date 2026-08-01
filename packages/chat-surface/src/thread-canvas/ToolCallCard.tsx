import { useState, type CSSProperties, type ReactElement } from "react";

import {
  ACTIVITY_CARD_INTERACTION_CSS,
  activityCardChevronStyle,
  activityCardDetailStyle,
  activityCardFrameStyle,
  activityCardHeaderStyle,
  activityCardMetaStyle,
  activityCardStaticHeaderStyle,
  activityCardTileStyle,
} from "../activity/ActivityCardChrome";
import type { ToolCallEntry } from "./eventProjector";

/**
 * Compact, portable presentation for one projected main-agent tool call.
 *
 * The card deliberately consumes the local projection rather than raw runtime
 * events. That keeps runtime payload compatibility at the projector boundary
 * and lets both transcript modes use the exact same disclosure and payload
 * safety policy.
 */
export interface ToolCallCardProps {
  readonly toolCall: ToolCallEntry;
  /**
   * The run is parked on a decision the user owes. A `running` card is then not
   * running: the graph is interrupted, so nothing is executing and the call is
   * not "taking a while" — it is stopped, on them.
   *
   * Same reasoning as `TcTodoList`'s `blocked`, and it applies to EVERY running
   * card rather than only the gated one: a call still in `running` while the
   * graph is interrupted is, by definition, not progressing.
   */
  readonly parked?: boolean;
}

/**
 * The tool header is a real native disclosure summary whenever it has safe
 * detail to reveal. Keeping the entire visual header inside `summary` makes
 * pointer, keyboard Enter, and keyboard Space target the same element.
 */
export function ToolCallCard({
  toolCall,
  parked = false,
}: ToolCallCardProps): ReactElement {
  const hasDetails = hasToolDetails(toolCall);
  const [detailsOpen, setDetailsOpen] = useState(false);
  // Only a call that is still running can be parked; a finished one is history.
  const waiting = parked && toolCall.status === "running";
  const header = renderHeader(toolCall, hasDetails, waiting);

  if (!hasDetails) {
    return (
      <div
        className="tc-activity-card"
        style={activityCardFrameStyle}
        role="group"
        aria-label={`Tool: ${toolCall.title}`}
        data-tool-status={toolCall.status}
        data-tool-waiting={waiting ? "true" : "false"}
      >
        <style>{TOOL_CALL_CARD_CSS}</style>
        {header}
      </div>
    );
  }

  return (
    <details
      className="tc-activity-card"
      style={activityCardFrameStyle}
      aria-label={`Tool: ${toolCall.title}`}
      data-tool-status={toolCall.status}
      data-tool-waiting={waiting ? "true" : "false"}
      onToggle={(event) => setDetailsOpen(event.currentTarget.open)}
    >
      <style>{`${ACTIVITY_CARD_INTERACTION_CSS}\n${TOOL_CALL_CARD_CSS}`}</style>
      <summary
        className="tc-tool-card__summary tc-activity-card__head"
        style={activityCardHeaderStyle}
        aria-label={`${detailsOpen ? "Hide" : "Show"} details for ${toolCall.title}`}
      >
        {header}
      </summary>
      <ToolCallDetails toolCall={toolCall} />
    </details>
  );
}

function renderHeader(
  toolCall: ToolCallEntry,
  discloseable: boolean,
  waiting: boolean,
): ReactElement {
  const running = toolCall.status === "running" && !waiting;
  const statusLabel = waiting ? "Waiting" : statusText(toolCall.status);
  const provenance = provenanceLabel(toolCall);
  const access = accessLabel(toolCall.accessMode);
  const duration = formatDuration(toolCall.durationMs);
  // A declined capability carries its explanation on `safe_message` exactly
  // like a failure does — and that sentence is the whole value of the card,
  // since it says what to do instead. It renders in the neutral style below.
  const summary =
    toolCall.status === "error" || toolCall.status === "unavailable"
      ? (toolCall.errorMessage ?? toolCall.summary)
      : toolCall.summary;

  return (
    <div
      style={discloseable ? summaryHeaderStyle : activityCardStaticHeaderStyle}
    >
      <span style={activityCardTileStyle} aria-hidden="true">
        {toolTileGlyph(toolCall.toolName)}
      </span>
      <span style={headerCopyStyle}>
        <span style={identityLineStyle}>
          <span style={toolTitleStyle}>{toolCall.title}</span>
          {provenance !== null ? (
            <span style={provenanceStyle}>{provenance}</span>
          ) : null}
          {access !== null ? <span style={toolMetaStyle}>{access}</span> : null}
          {duration !== null ? (
            <span style={activityCardMetaStyle}>{duration}</span>
          ) : null}
        </span>
        {summary !== undefined ? (
          <span
            style={
              toolCall.status === "error"
                ? errorSummaryTextStyle
                : summaryTextStyle
            }
          >
            {summary}
          </span>
        ) : null}
      </span>
      <span
        style={statusGroupStyle}
        aria-label={statusLabel}
        data-testid={`tc-chat-tool-${toolCall.id}-status`}
      >
        <span
          style={waiting ? waitingMarkStyle : statusMarkStyle(toolCall.status)}
          aria-hidden="true"
        >
          {waiting ? (
            // Still, not spinning — the whole point. Same amber arc the parked
            // todo row uses, so one glance reads the same across both surfaces.
            <span
              data-testid="tc-tool-card-waiting"
              style={waitingGlyphStyle}
            />
          ) : running ? (
            <span className="tc-tool-card__spinner" style={spinnerStyle} />
          ) : toolCall.status === "error" ? (
            "!"
          ) : toolCall.status === "unavailable" ? (
            "—"
          ) : (
            "✓"
          )}
        </span>
        <span style={waiting ? waitingLabelStyle : statusLabelStyle}>
          {statusLabel}
        </span>
      </span>
      {discloseable ? (
        <span
          className="tc-tool-card__chevron tc-activity-card__chevron"
          style={activityCardChevronStyle}
          aria-hidden="true"
        >
          ▾
        </span>
      ) : null}
    </div>
  );
}

function ToolCallDetails({ toolCall }: ToolCallCardProps): ReactElement {
  const source = sourceLabel(toolCall);
  return (
    <div
      style={activityCardDetailStyle}
      data-testid={`tc-chat-tool-${toolCall.id}-details`}
    >
      {toolCall.title !== toolCall.toolName ? (
        <DetailRow label="tool" value={toolCall.toolName} />
      ) : null}
      {toolCall.args !== undefined ? (
        <PayloadRow
          label="args"
          value={toolCall.args}
          testId={`tc-chat-tool-${toolCall.id}-args`}
        />
      ) : null}
      {toolCall.result !== undefined ? (
        <PayloadRow
          label="result"
          value={toolCall.result}
          testId={`tc-chat-tool-${toolCall.id}-result`}
        />
      ) : null}
      {source !== null ? <DetailRow label="source" value={source} /> : null}
      {toolCall.errorMessage !== undefined ? (
        <DetailRow label="error" value={toolCall.errorMessage} tone="error" />
      ) : null}
      {toolCall.subagentTaskIds !== undefined &&
      toolCall.subagentTaskIds.length > 0 ? (
        <DelegatedWorkRow taskIds={toolCall.subagentTaskIds} />
      ) : null}
    </div>
  );
}

function PayloadRow({
  label,
  value,
  testId,
}: {
  readonly label: "args" | "result";
  readonly value: Record<string, unknown>;
  readonly testId: string;
}): ReactElement {
  const payload = formatPayload(value);
  return (
    <div style={detailRowStyle}>
      <span style={detailLabelStyle}>{label}</span>
      <span style={detailValueStyle}>
        <pre
          style={payloadStyle}
          data-testid={testId}
          data-truncated={payload.truncated ? "true" : "false"}
          tabIndex={0}
          aria-label={
            payload.truncated
              ? `${label} payload, truncated to ${TOOL_PAYLOAD_CAP} characters; displayed content can be selected and copied`
              : `${label} payload; content can be selected and copied`
          }
          title={
            payload.truncated
              ? `Payload truncated to ${TOOL_PAYLOAD_CAP} characters for the transcript. Select and copy the displayed content.`
              : "Select and copy the displayed content."
          }
        >
          {payload.text}
        </pre>
      </span>
    </div>
  );
}

function DetailRow({
  label,
  value,
  tone = "default",
}: {
  readonly label: "tool" | "source" | "error";
  readonly value: string;
  readonly tone?: "default" | "error";
}): ReactElement {
  return (
    <div style={detailRowStyle}>
      <span style={detailLabelStyle}>{label}</span>
      <span style={tone === "error" ? errorValueStyle : detailValueStyle}>
        {value}
      </span>
    </div>
  );
}

function DelegatedWorkRow({
  taskIds,
}: {
  readonly taskIds: readonly string[];
}): ReactElement {
  const count = taskIds.length;
  return (
    <div style={detailRowStyle} data-testid="tc-chat-tool-delegated-work">
      <span style={detailLabelStyle}>children</span>
      <span style={delegatedWorkStyle}>
        <span style={delegatedCountStyle}>
          {count} delegated {count === 1 ? "task" : "tasks"}
        </span>
        {taskIds.map((taskId) => (
          <a
            key={taskId}
            href={`#subagent-task-${taskId}`}
            data-subagent-task-id={taskId}
            style={delegatedLinkStyle}
          >
            {taskId}
          </a>
        ))}
      </span>
    </div>
  );
}

function hasToolDetails(toolCall: ToolCallEntry): boolean {
  return (
    toolCall.args !== undefined ||
    toolCall.result !== undefined ||
    toolCall.title !== toolCall.toolName ||
    toolCall.provenance !== undefined ||
    toolCall.errorMessage !== undefined ||
    (toolCall.subagentTaskIds?.length ?? 0) > 0
  );
}

function provenanceLabel(toolCall: ToolCallEntry): string | null {
  const provenance = toolCall.provenance;
  if (provenance === undefined) return null;
  return `MCP · ${provenance.serverName}`;
}

function sourceLabel(toolCall: ToolCallEntry): string | null {
  const provenance = toolCall.provenance;
  if (provenance === undefined) return null;
  return `${provenance.source.toUpperCase()} · ${provenance.serverName}`;
}

function accessLabel(accessMode: ToolCallEntry["accessMode"]): string | null {
  switch (accessMode) {
    case "read":
      return "read";
    case "read_act":
      return "read + act";
    case "off":
      return "off";
    default:
      return null;
  }
}

function formatDuration(durationMs: number | undefined): string | null {
  if (
    durationMs === undefined ||
    !Number.isFinite(durationMs) ||
    durationMs < 0
  ) {
    return null;
  }
  if (durationMs < 1000) return `${Math.round(durationMs)} ms`;
  if (durationMs < 60_000) {
    const seconds = durationMs / 1000;
    return `${seconds % 1 === 0 ? seconds : seconds.toFixed(1)}s`;
  }
  const minutes = durationMs / 60_000;
  return `${minutes % 1 === 0 ? minutes : minutes.toFixed(1)}m`;
}

function statusText(status: ToolCallEntry["status"]): string {
  switch (status) {
    case "running":
      return "Running";
    case "complete":
      return "Done";
    case "error":
      return "Failed";
    case "unavailable":
      // Neither "Done" (no work happened) nor "Failed" (nothing broke).
      return "Not available";
  }
}

function toolTileGlyph(toolName: string): string {
  const initial = toolName.trim().at(0);
  return initial === undefined ? "•" : initial.toUpperCase();
}

const TOOL_PAYLOAD_CAP = 600;

function formatPayload(value: Record<string, unknown>): {
  readonly text: string;
  readonly truncated: boolean;
} {
  let text: string;
  try {
    text = JSON.stringify(value, null, 2);
  } catch {
    return { text: "[unserialisable]", truncated: false };
  }
  if (text.length <= TOOL_PAYLOAD_CAP) return { text, truncated: false };
  return {
    text: `${text.slice(0, TOOL_PAYLOAD_CAP)}…`,
    truncated: true,
  };
}

// `summary` owns the flex layout for discloseable cards. The header wrapper
// then disappears from layout without changing the child structure used by
// regular, non-discloseable tool cards.
const summaryHeaderStyle: CSSProperties = { display: "contents" };

const headerCopyStyle: CSSProperties = {
  display: "flex",
  flex: "1 1 auto",
  flexDirection: "column",
  minWidth: 0,
};

const identityLineStyle: CSSProperties = {
  alignItems: "baseline",
  display: "flex",
  flexWrap: "wrap",
  gap: "3px 7px",
  minWidth: 0,
};

const toolTitleStyle: CSSProperties = {
  color: "var(--color-text)",
  fontFamily: "var(--font-sans)",
  fontSize: "var(--font-size-sm)",
  fontWeight: 500,
  lineHeight: "18px",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const toolMetaStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-9-5)",
  lineHeight: "13px",
  whiteSpace: "nowrap",
};

/** The source-backed MCP identity is intentionally a separate compact chip;
 * access mode and elapsed time remain quieter adjacent metadata. */
const provenanceStyle: CSSProperties = {
  alignItems: "center",
  border: "1px solid var(--color-accent-line)",
  borderRadius: 5,
  color: "var(--color-accent)",
  display: "inline-flex",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-8-5)",
  fontWeight: 400,
  gap: 4,
  letterSpacing: "0.04em",
  lineHeight: "12.75px",
  padding: "2px 6px",
  whiteSpace: "nowrap",
};

const summaryTextStyle: CSSProperties = {
  color: "var(--color-text-strong)",
  fontFamily: "var(--font-sans)",
  fontSize: "var(--font-size-mono-10-5)",
  lineHeight: "15.75px",
  marginTop: 3,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const errorSummaryTextStyle: CSSProperties = {
  ...summaryTextStyle,
  color: "var(--color-danger)",
};

const statusGroupStyle: CSSProperties = {
  alignItems: "center",
  display: "inline-flex",
  flex: "0 0 auto",
  gap: 4,
};

const statusLabelStyle: CSSProperties = {
  color: "var(--color-text-muted)",
  fontFamily: "var(--font-sans)",
  fontSize: "var(--font-size-2xs)",
  fontWeight: 500,
  lineHeight: "14px",
  whiteSpace: "nowrap",
};

const waitingLabelStyle: CSSProperties = {
  ...statusLabelStyle,
  color: "var(--color-warning, #e8b45e)",
};

const statusMarkStyle = (status: ToolCallEntry["status"]): CSSProperties => ({
  alignItems: "center",
  color:
    status === "error"
      ? "var(--color-danger)"
      : status === "complete"
        ? "var(--color-success)"
        : "var(--color-text-subtle)",
  display: "inline-flex",
  flex: "0 0 auto",
  fontFamily: "var(--font-mono)",
  fontSize: 11,
  fontWeight: 700,
  height: 14,
  justifyContent: "center",
  lineHeight: 1,
  width: 14,
});

const spinnerStyle: CSSProperties = {
  border: "1.5px solid var(--color-border-strong)",
  borderRadius: "50%",
  borderTopColor: "var(--color-accent)",
  boxSizing: "border-box",
  height: 14,
  width: 14,
};

/** The parked mark: the same amber the parked todo row uses, and no animation. */
const waitingMarkStyle: CSSProperties = {
  alignItems: "center",
  color: "var(--color-warning, #e8b45e)",
  display: "inline-flex",
  flex: "0 0 auto",
  height: 14,
  justifyContent: "center",
  width: 14,
};

const waitingGlyphStyle: CSSProperties = {
  border: "1.5px solid var(--color-warning, #e8b45e)",
  borderRadius: "50%",
  borderTopColor: "transparent",
  boxSizing: "border-box",
  height: 12,
  width: 12,
};

const detailRowStyle: CSSProperties = {
  display: "grid",
  gap: 8,
  gridTemplateColumns: "66px minmax(0, 1fr)",
  minWidth: 0,
  paddingTop: 7,
};

const detailLabelStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-9-5)",
  lineHeight: "14px",
  paddingTop: 2,
};

const detailValueStyle: CSSProperties = {
  color: "var(--color-text-strong)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-10)",
  lineHeight: "15px",
  minWidth: 0,
};

const errorValueStyle: CSSProperties = {
  ...detailValueStyle,
  color: "var(--color-danger)",
};

const payloadStyle: CSSProperties = {
  ...detailValueStyle,
  background: "transparent",
  border: 0,
  margin: 0,
  maxHeight: 150,
  overflow: "auto",
  padding: 0,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
};

const delegatedWorkStyle: CSSProperties = {
  alignItems: "center",
  display: "flex",
  flexWrap: "wrap",
  gap: 6,
  minWidth: 0,
};

const delegatedCountStyle: CSSProperties = {
  ...detailValueStyle,
  color: "var(--color-text-muted)",
};

const delegatedLinkStyle: CSSProperties = {
  border: "1px solid var(--color-border-strong)",
  borderRadius: 5,
  color: "var(--color-text-strong)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-9-5)",
  lineHeight: "14px",
  maxWidth: "100%",
  overflow: "hidden",
  padding: "1px 5px",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const TOOL_CALL_CARD_CSS = `
@keyframes tc-tool-card-spin { to { transform: rotate(360deg); } }
.tc-tool-card__spinner { animation: tc-tool-card-spin 0.7s linear infinite; }
[data-reduce-motion="1"] .tc-tool-card__spinner,
[data-reduce-motion="always"] .tc-tool-card__spinner { animation: none; }
@media (prefers-reduced-motion: reduce) {
  .tc-tool-card__spinner { animation: none; }
}
`;
