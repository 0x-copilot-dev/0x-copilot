import type { CSSProperties, ReactElement } from "react";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

export type FocusPlanStepState = "complete" | "active" | "upcoming";

export interface FocusPlanStep {
  readonly id: string;
  readonly label: string;
  readonly state: FocusPlanStepState;
}

export interface FocusPlanProjection {
  readonly steps: readonly FocusPlanStep[];
}

/**
 * A compact, honest Focus-mode plan. The client never invents future work from
 * a prompt: it reflects the active reasoning phase and the tools the runtime
 * has actually scheduled. This keeps Focus useful before a tool starts while
 * still making every later step traceable to the canonical run event stream.
 */
export function projectFocusPlan(
  events: readonly RuntimeEventEnvelope[],
): FocusPlanProjection {
  const steps = new Map<string, FocusPlanStep>();
  let sawReasoning = false;

  for (const event of events) {
    if (event.event_type === "reasoning_summary_delta") {
      sawReasoning = true;
      continue;
    }

    if (
      event.event_type !== "tool_call_started" &&
      event.event_type !== "tool_call_delta" &&
      event.event_type !== "tool_result"
    ) {
      continue;
    }

    const payload = recordValue(event.payload);
    const callId = stringValue(payload?.call_id) ?? event.event_id;
    const existing = steps.get(callId);
    const label =
      agentToolTitle(payload) ?? existing?.label ?? toolLabel(event, payload);
    const completed = event.event_type === "tool_result";
    steps.set(callId, {
      id: callId,
      label,
      state: completed ? "complete" : "active",
    });
  }

  const scheduled = [...steps.values()].slice(0, 4);
  if (scheduled.length > 0) {
    const activeIndex = scheduled.findIndex((step) => step.state === "active");
    return {
      steps: scheduled.map((step, index) =>
        step.state === "active" && index !== activeIndex
          ? { ...step, state: "upcoming" }
          : step,
      ),
    };
  }

  return {
    steps: [
      sawReasoning
        ? {
            id: "understanding-request",
            label: "Understanding your request",
            state: "active",
          }
        : {
            id: "awaiting-plan",
            label: "Awaiting the agent plan",
            state: "upcoming",
          },
    ],
  };
}

export function FocusPlan({
  projection,
}: {
  readonly projection: FocusPlanProjection;
}): ReactElement {
  return (
    <section
      aria-labelledby="focus-plan-heading"
      data-testid="focus-plan"
      style={rootStyle}
    >
      <h3 id="focus-plan-heading" style={headingStyle}>
        Plan
      </h3>
      <ol style={listStyle}>
        {projection.steps.map((step) => (
          <li
            key={step.id}
            data-state={step.state}
            style={stepStyle(step.state)}
          >
            <PlanGlyph state={step.state} />
            <span style={labelStyle}>{step.label}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function PlanGlyph({
  state,
}: {
  readonly state: FocusPlanStepState;
}): ReactElement {
  if (state === "complete") {
    return (
      <svg aria-hidden="true" style={glyphStyle(state)} viewBox="0 0 16 16">
        <path
          d="m3.5 8.25 2.75 2.75 6.25-6.25"
          fill="none"
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.7"
        />
      </svg>
    );
  }
  if (state === "active") {
    return <span aria-hidden="true" style={activeGlyphStyle} />;
  }
  return (
    <svg aria-hidden="true" style={glyphStyle(state)} viewBox="0 0 16 16">
      <path
        d="m6.25 3.75 4.5 4.25-4.5 4.25"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.7"
      />
    </svg>
  );
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function toolLabel(
  event: RuntimeEventEnvelope,
  payload: Record<string, unknown> | null,
): string {
  const agentTitle = agentToolTitle(payload);
  if (agentTitle !== null) return agentTitle;
  const presentationTitle = stringValue(event.presentation?.title);
  if (presentationTitle !== null) return presentationTitle;
  const title = stringValue(event.display_title);
  if (title !== null) return title;
  const toolName = stringValue(payload?.tool_name);
  if (toolName === null) return "Running a tool";
  return toolName
    .replace(/[._-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function agentToolTitle(
  payload: Record<string, unknown> | null,
): string | null {
  const args = recordValue(payload?.args);
  return stringValue(args?.display_title) ?? stringValue(args?._display_title);
}

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  margin: "9px 0 0",
  padding: 0,
};

const headingStyle: CSSProperties = {
  margin: "6px 0 -3px",
  color: "var(--color-text-subtle)",
  fontFamily: "var(--font-mono)",
  fontSize: 9.5,
  fontWeight: 400,
  letterSpacing: "0.12em",
  textTransform: "uppercase",
};

const listStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  listStyle: "none",
  margin: 0,
  padding: 0,
};

const stepStyle = (state: FocusPlanStepState): CSSProperties => ({
  display: "flex",
  alignItems: "flex-start",
  gap: 9,
  color:
    state === "upcoming"
      ? "var(--color-text-subtle)"
      : "var(--color-text-muted)",
  fontSize: 12,
  lineHeight: 1.35,
});

const labelStyle: CSSProperties = {
  minWidth: 0,
};

const glyphStyle = (
  state: Exclude<FocusPlanStepState, "active">,
): CSSProperties => ({
  flex: "0 0 auto",
  width: 15,
  height: 15,
  marginTop: 1,
  color:
    state === "complete"
      ? "var(--color-success, #57c785)"
      : "var(--color-text-subtle)",
});

const activeGlyphStyle: CSSProperties = {
  flex: "0 0 auto",
  width: 8,
  height: 8,
  margin: "4px 3.5px 0",
  borderRadius: "50%",
  background: "var(--color-accent)",
};
