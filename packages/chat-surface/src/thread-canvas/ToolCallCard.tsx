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
import type { ToolCallBlock, ToolCallEntry } from "./eventProjector";
import { toolViewFor, type ToolView } from "./toolViews";

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
   *
   * RUN-WIDE, and that is the limit this card also reads `toolCall.blockedBy`
   * for: this flag says a decision is pending SOMEWHERE, so every open call
   * wearing it reads the same. The card the decision is actually about is named
   * by the projection, and only that one is told the reader owns it.
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
  const view = toolViewFor(toolCall.toolName);
  const hasDetails = hasToolDetails(toolCall);
  // A file-change card opens itself. `defaultOpen` is uncontrolled on purpose —
  // it seeds the native <details> and the user's own toggle wins thereafter.
  const [detailsOpen, setDetailsOpen] = useState(view.defaultOpen);
  const decision = pendingDecision(toolCall);
  // Only a call that is still running can be parked; a finished one is history.
  // The gated call is parked on its own account, so it no longer depends on the
  // host having threaded `parked` down to know it.
  const waiting =
    (parked || decision !== null) && toolCall.status === "running";
  const header = renderHeader(toolCall, hasDetails, waiting, decision, view);
  // Two stopped cards that look alike but want opposite things from the reader.
  // Stamped at the card ROOT so a journey can assert which one is drawn without
  // reading copy, the way `data-tool-status` already works.
  const blocked = toolCall.blockedBy?.kind ?? null;

  if (!hasDetails) {
    return (
      <div
        className="tc-activity-card"
        style={activityCardFrameStyle}
        role="group"
        aria-label={`Tool: ${toolCall.title}`}
        data-tool-status={toolCall.status}
        data-tool-waiting={waiting ? "true" : "false"}
        {...(blocked === null ? {} : { "data-tool-blocked": blocked })}
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
      {...(blocked === null ? {} : { "data-tool-blocked": blocked })}
      // CONTROLLED, seeded from the view. A bare `open={true}` would be
      // re-applied on every render and the reader could never collapse the
      // card; driving it from state means `defaultOpen` only chooses the
      // STARTING position and the user's own toggle wins from then on.
      open={detailsOpen}
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
  decision: PendingDecision | null,
  view: ToolView,
): ReactElement {
  const running = toolCall.status === "running" && !waiting;
  // "Waiting" alone was the bug. It is true of every open call while the graph
  // is interrupted, so it reads as "waiting on the tool" — which is exactly the
  // wrong thing to conclude about the one call that is waiting on the READER.
  const statusLabel =
    decision !== null
      ? "Needs you"
      : waiting
        ? "Waiting"
        : statusText(toolCall.status);
  // Derived from the call's own arguments, so it is available as soon as they
  // finish streaming — for a file tool this is the single most useful fact on
  // the row, and it used to be buried inside the raw JSON payload.
  const subtitle = view.subtitle(toolCall);
  const provenance = provenanceLabel(toolCall);
  const access = accessLabel(toolCall.accessMode);
  const duration = formatDuration(toolCall.durationMs);
  // A declined capability carries its explanation on `safe_message` exactly
  // like a failure does — and that sentence is the whole value of the card,
  // since it says what to do instead. It renders in the neutral style below.
  //
  // A gated call has no such sentence yet, because nothing has gone wrong: its
  // headline is the server's own question ("Allow writing to /a/b.csv?"), which
  // is the closest thing on the wire to "what is being decided".
  const summary =
    decision !== null
      ? (decision.ask ?? DECISION_FALLBACK)
      : toolCall.status === "error" || toolCall.status === "unavailable"
        ? (toolCall.errorMessage ?? toolCall.summary)
        : toolCall.summary;
  const remedy = remedyFor(toolCall.blockedBy, decision);

  return (
    <div
      style={discloseable ? summaryHeaderStyle : activityCardStaticHeaderStyle}
    >
      <span style={activityCardTileStyle} aria-hidden="true">
        {/* A registered tool gets its own glyph; the long tail of connector
            tools keeps the letter tile, which at least tells two unfamiliar
            tool names apart. */}
        {view.icon ?? toolTileGlyph(toolCall.toolName)}
      </span>
      <span style={headerCopyStyle}>
        <span style={identityLineStyle}>
          <span style={toolTitleStyle}>{toolCall.title}</span>
          {subtitle !== null ? (
            <span style={subtitleStyle} data-testid="tc-tool-card-subtitle">
              {subtitle}
            </span>
          ) : null}
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
              decision !== null
                ? waitingSummaryTextStyle
                : toolCall.status === "error"
                  ? errorSummaryTextStyle
                  : summaryTextStyle
            }
            data-testid={
              decision === null ? undefined : `tc-tool-card-ask-${toolCall.id}`
            }
          >
            {summary}
          </span>
        ) : null}
        {/* The remedy, and it is the reason this row exists: the reason a card
            gives ("permission denied for write on /random.csv") names the wall
            and not the door. It sits in the COLLAPSED header on purpose — a
            reader who has to open a disclosure to learn the run is waiting on
            them has already concluded it is broken. */}
        {remedy !== null ? (
          <span
            style={remedyTextStyle}
            data-testid={`tc-tool-card-remedy-${toolCall.id}`}
          >
            {remedy}
          </span>
        ) : null}
      </span>
      <span
        style={statusGroupStyle}
        // The label is clipped to the rail's width; the announcement is not, so
        // it carries the whole fact rather than the abbreviation of it.
        aria-label={decision !== null ? DECISION_STATUS_LABEL : statusLabel}
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
  const decision = pendingDecision(toolCall);
  const Body = toolViewFor(toolCall.toolName).Body;
  const specialised = Body === null ? null : <Body toolCall={toolCall} />;

  const payloadRows = (
    <>
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
    </>
  );
  const hasPayload =
    toolCall.args !== undefined || toolCall.result !== undefined;

  return (
    <div
      style={activityCardDetailStyle}
      data-testid={`tc-chat-tool-${toolCall.id}-details`}
    >
      {specialised}
      {/* Named, not decided. The one ask card in this package is
          `TcWriteGateRow`, reached through `renderApprovalItem`, and a second
          approve control — even one deferred behind a disclosure — would put an
          irreversible write one click from a surface whose testid scheme the
          safety journeys do not police. What the row adds is the id, so the
          decision on screen and the call it belongs to are joinable by eye. */}
      {decision !== null ? (
        <DetailRow
          label="decision"
          value={decision.approvalId}
          testId={`tc-tool-card-decision-${toolCall.id}`}
        />
      ) : null}
      {toolCall.title !== toolCall.toolName ? (
        <DetailRow label="tool" value={toolCall.toolName} />
      ) : null}
      {/* With a specialised view the raw payload is demoted, not deleted: the
          view is a reading of the call, and the JSON remains the record of what
          actually crossed the wire. Without one it stays the primary body. */}
      {specialised === null ? (
        payloadRows
      ) : hasPayload ? (
        <details style={rawPayloadStyle}>
          <summary style={rawPayloadSummaryStyle}>raw payload</summary>
          {payloadRows}
        </details>
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
  testId,
}: {
  readonly label: "tool" | "source" | "error" | "decision";
  readonly value: string;
  readonly tone?: "default" | "error";
  readonly testId?: string;
}): ReactElement {
  return (
    <div
      style={detailRowStyle}
      {...(testId === undefined ? {} : { "data-testid": testId })}
    >
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
    toolCall.blockedBy?.kind === "decision" ||
    (toolCall.subagentTaskIds?.length ?? 0) > 0
  );
}

/** The `decision` arm, narrowed once so the three readers agree by type rather
 *  than by three copies of the same `kind ===` test. */
type PendingDecision = Extract<ToolCallBlock, { kind: "decision" }>;

function pendingDecision(toolCall: ToolCallEntry): PendingDecision | null {
  const block = toolCall.blockedBy;
  return block !== undefined && block.kind === "decision" ? block : null;
}

/** Said only when the wire gave the gate no question of its own. */
const DECISION_FALLBACK = "This step is waiting for your decision.";

/** What a screen reader hears where the rail shows the clipped "Needs you". */
const DECISION_STATUS_LABEL = "Waiting for your approval";

/**
 * What the reader must DO — one line, and the point of the whole change.
 *
 * The gated line says the run is paused rather than pointing at a location:
 * the ask card interleaves into the transcript below this one, but it is hidden
 * while the transcript is scrubbed, and a card that promises a control that is
 * not currently drawn is worse than one that only states the fact. Reachability
 * itself already exists and is always on screen while anything is pending —
 * `TcChat`'s pinned "N approvals waiting ↓", which scrolls to the ask.
 *
 * The denied lines name the authority that was withheld, because the coarsened
 * refusal ("permission denied for write on /random.csv") names the wall only.
 */
function remedyFor(
  block: ToolCallBlock | undefined,
  decision: PendingDecision | null,
): string | null {
  if (decision !== null) {
    return "Paused — the run continues once you approve or decline it.";
  }
  if (block?.kind !== "permission") {
    return null;
  }
  return block.lane === "filesystem"
    ? "Attach that folder to this chat to allow it."
    : "Check this connector's access under Tools.";
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

/** The filename / pattern chip. Mono because it is a machine identifier, and
 *  quieter than the title so the row still reads title-first. */
const subtitleStyle: CSSProperties = {
  color: "var(--color-text-muted)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-10)",
  lineHeight: "15px",
  maxWidth: "22ch",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const rawPayloadStyle: CSSProperties = {
  borderTop: "1px solid var(--color-border)",
  marginTop: 8,
  paddingTop: 4,
};

const rawPayloadSummaryStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
  cursor: "pointer",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-9-5)",
  letterSpacing: "0.04em",
  lineHeight: "16px",
  listStyle: "revert",
  userSelect: "none",
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

/** The gate's own question. Amber, not red — nothing has failed here, which is
 *  the entire distinction this card exists to draw. */
const waitingSummaryTextStyle: CSSProperties = {
  ...summaryTextStyle,
  color: "var(--color-warning, #e8b45e)",
};

/** The remedy sits UNDER the reason and quieter than it: the reason is the fact
 *  and keeps its colour, this is the instruction. Wrapping is allowed — one
 *  ellipsised half-sentence telling someone what to do is worse than two
 *  lines — while the reason above stays clamped to one line. */
const remedyTextStyle: CSSProperties = {
  color: "var(--color-text-muted)",
  fontFamily: "var(--font-sans)",
  fontSize: "var(--font-size-2xs)",
  lineHeight: "14px",
  marginTop: 2,
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
