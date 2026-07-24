import type { CSSProperties, ReactElement } from "react";

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
}

/**
 * The tool header is a real native disclosure summary whenever it has safe
 * detail to reveal. Keeping the entire visual header inside `summary` makes
 * pointer, keyboard Enter, and keyboard Space target the same element.
 */
export function ToolCallCard({ toolCall }: ToolCallCardProps): ReactElement {
  const hasDetails = hasToolDetails(toolCall);
  const header = renderHeader(toolCall, hasDetails);

  if (!hasDetails) {
    return (
      <div
        style={cardStyle}
        role="group"
        aria-label={`Tool: ${toolCall.title}`}
        data-tool-status={toolCall.status}
      >
        {header}
      </div>
    );
  }

  return (
    <details
      style={cardStyle}
      aria-label={`Tool: ${toolCall.title}`}
      data-tool-status={toolCall.status}
    >
      <style>{TOOL_CALL_CARD_CSS}</style>
      <summary
        className="tc-tool-card__summary"
        style={summaryControlStyle}
        aria-label={`Show details for ${toolCall.title}`}
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
): ReactElement {
  const running = toolCall.status === "running";
  const statusLabel = statusText(toolCall.status);
  const provenance = provenanceLabel(toolCall);
  const access = accessLabel(toolCall.accessMode);
  const duration = formatDuration(toolCall.durationMs);

  return (
    <div style={headerStyle}>
      <span style={tileStyle} aria-hidden="true">
        {toolTileGlyph(toolCall.toolName)}
      </span>
      <span style={headerCopyStyle}>
        <span style={identityLineStyle}>
          <span style={toolNameStyle}>{toolCall.toolName}</span>
          {provenance !== null ? (
            <span style={provenanceStyle}>{provenance}</span>
          ) : null}
          {access !== null ? <span style={toolMetaStyle}>{access}</span> : null}
          {duration !== null ? (
            <span style={toolDurationStyle}>{duration}</span>
          ) : null}
        </span>
        {toolCall.summary !== undefined ? (
          <span style={summaryTextStyle}>{toolCall.summary}</span>
        ) : null}
      </span>
      <span style={statusMarkStyle(toolCall.status)} aria-label={statusLabel}>
        {running ? (
          <span className="tc-tool-card__spinner" style={spinnerStyle} />
        ) : toolCall.status === "error" ? (
          "!"
        ) : (
          "✓"
        )}
      </span>
      {discloseable ? (
        <span
          className="tc-tool-card__chevron"
          style={chevronStyle}
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
      style={detailBodyStyle}
      data-testid={`tc-chat-tool-${toolCall.id}-details`}
    >
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
  readonly label: "source" | "error";
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
      return "Completed";
    case "error":
      return "Failed";
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

const cardStyle: CSSProperties = {
  background: "var(--color-surface)",
  border: "1px solid var(--color-border)",
  borderRadius: 10,
  color: "var(--color-text)",
  fontSize: 13,
  lineHeight: "19.5px",
  margin: 0,
  overflow: "hidden",
};

const summaryControlStyle: CSSProperties = {
  cursor: "pointer",
  listStyle: "none",
  userSelect: "none",
};

const headerStyle: CSSProperties = {
  alignItems: "center",
  display: "flex",
  gap: 9,
  minWidth: 0,
  padding: "9px 11px",
};

const tileStyle: CSSProperties = {
  alignItems: "center",
  background: "var(--color-surface-elevated)",
  borderRadius: 6,
  color: "var(--color-accent)",
  display: "inline-flex",
  flex: "0 0 auto",
  fontFamily: "var(--font-sans)",
  fontSize: 9,
  fontWeight: 700,
  height: 22,
  justifyContent: "center",
  lineHeight: 1,
  width: 22,
};

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

const toolNameStyle: CSSProperties = {
  color: "var(--color-text)",
  fontFamily: "var(--font-mono)",
  fontSize: 11,
  fontWeight: 500,
  lineHeight: "15px",
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

const toolDurationStyle: CSSProperties = {
  ...toolMetaStyle,
  fontSize: 9,
  lineHeight: "13.5px",
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

const chevronStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
  flex: "0 0 auto",
  fontSize: 11,
  lineHeight: 1,
  width: 10,
};

const detailBodyStyle: CSSProperties = {
  background: "var(--color-bg-elevated)",
  borderTop: "1px solid var(--color-border)",
  display: "block",
  padding: "10px 12px",
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
.tc-tool-card__summary::-webkit-details-marker { display: none; }
.tc-tool-card__summary::marker { content: ""; }
.tc-tool-card__summary:hover { background: var(--color-surface-muted); }
.tc-tool-card__summary:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: -2px;
}
.tc-tool-card__summary .tc-tool-card__chevron { transition: transform 120ms ease; }
details[open] > .tc-tool-card__summary .tc-tool-card__chevron { transform: rotate(180deg); }
[data-reduce-motion="1"] .tc-tool-card__spinner,
[data-reduce-motion="always"] .tc-tool-card__spinner { animation: none; }
@media (prefers-reduced-motion: reduce) {
  .tc-tool-card__spinner,
  .tc-tool-card__summary .tc-tool-card__chevron { animation: none; transition: none; }
}
`;
