import type { CSSProperties, ReactElement } from "react";

/** Honest fallback for a staged universal effect without a specialized renderer. */
export function EffectStageCard(props: {
  readonly stageId: string;
  readonly title: string;
}): ReactElement {
  return (
    <section
      data-testid="effect-stage-card"
      data-stage-id={props.stageId}
      style={cardStyle}
    >
      <p style={eyebrowStyle}>PROPOSED CHANGE</p>
      <h2 style={titleStyle}>{props.title}</h2>
      <p style={copyStyle}>Review this change before it can be applied.</p>
    </section>
  );
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
