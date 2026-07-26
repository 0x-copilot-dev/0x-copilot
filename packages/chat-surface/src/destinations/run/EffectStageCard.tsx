import type { CSSProperties, ReactElement } from "react";

import type { McpEffectStageReview } from "./effectStageLifecycle";

/** Honest fallback for a staged universal effect without a specialized renderer. */
export function EffectStageCard(props: {
  readonly stageId: string;
  readonly title: string;
  /** Present only for the owner-scoped generic MCP effect decision path. */
  readonly review?: McpEffectStageReview;
  readonly busy?: boolean;
  readonly message?: string | null;
  readonly onDecision?: (
    stage: McpEffectStageReview,
    decision: "approve" | "reject",
  ) => void;
}): ReactElement {
  const review = props.review;
  const canDecide =
    review?.canDecide === true && props.onDecision !== undefined;
  return (
    <section
      data-testid="effect-stage-card"
      data-stage-id={props.stageId}
      style={cardStyle}
    >
      <p style={eyebrowStyle}>PROPOSED CHANGE</p>
      <h2 style={titleStyle}>{props.title}</h2>
      <p style={copyStyle}>{copyFor(review)}</p>
      {props.message !== null && props.message !== undefined ? (
        <p data-testid="effect-stage-message" style={copyStyle}>
          {props.message}
        </p>
      ) : null}
      {canDecide ? (
        <div style={actionsStyle}>
          <button
            type="button"
            data-testid="effect-stage-reject"
            disabled={props.busy}
            onClick={() => props.onDecision?.(review, "reject")}
          >
            Reject
          </button>
          <button
            type="button"
            data-testid="effect-stage-approve"
            disabled={props.busy}
            onClick={() => props.onDecision?.(review, "approve")}
          >
            {props.busy ? "Recording…" : "Approve"}
          </button>
        </div>
      ) : null}
    </section>
  );
}

function copyFor(review: McpEffectStageReview | undefined): string {
  switch (review?.status) {
    case "approved":
      return "Approved. The run will execute the exact reviewed revision.";
    case "rejected":
      return "Rejected. No external change will be made.";
    case "executing":
      return "Approved and executing the exact reviewed revision.";
    case "applied":
      return "Applied from the exact reviewed revision.";
    case "held":
      return "Details changed or are unavailable. Review cannot be recorded.";
    default:
      return "Review this change before it can be applied.";
  }
}

const cardStyle: CSSProperties = {
  display: "grid",
  gap: "var(--space-2, 8px)",
  minHeight: "100%",
  alignContent: "center",
  padding: "var(--space-8, 32px)",
  border: "1px solid var(--color-border, #30343d)",
  borderRadius: "var(--radius-lg, 12px)",
  background: "var(--color-surface, #161922)",
};
const eyebrowStyle: CSSProperties = {
  margin: 0,
  color: "var(--color-text-muted, #9aa1af)",
  fontFamily: "var(--font-mono)",
  fontWeight: 700,
};
const titleStyle: CSSProperties = {
  margin: 0,
  color: "var(--color-text, #f4f5f6)",
};
const copyStyle: CSSProperties = {
  margin: 0,
  color: "var(--color-text-muted, #9aa1af)",
};
const actionsStyle: CSSProperties = {
  display: "flex",
  gap: "var(--space-2, 8px)",
};
