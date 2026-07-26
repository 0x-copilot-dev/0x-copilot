import type { CSSProperties, ReactElement } from "react";

import type {
  CanvasLifecycleProjection,
  CanvasLifecycleSubject,
} from "./canvasLifecycle";

/** Compact Focus cards; they render the same lifecycle projection as Studio. */
export function CanvasFocusCards(props: {
  readonly projection: CanvasLifecycleProjection;
  readonly onOpenSubject: (subjectKey: string) => void;
}): ReactElement | null {
  const cards = props.projection.tabs;
  const hasGate = props.projection.pendingSubjectKeys.some((key) =>
    key.startsWith("gate:"),
  );
  if (cards.length === 0 && !hasGate) return null;
  return (
    <section
      aria-label="Run review cards"
      data-testid="canvas-focus-cards"
      style={stackStyle}
    >
      {cards.map((subject) => (
        <FocusCard
          key={subject.key}
          subject={subject}
          onOpenSubject={props.onOpenSubject}
        />
      ))}
      {hasGate ? <GateFocusCard /> : null}
    </section>
  );
}

function FocusCard(props: {
  readonly subject: CanvasLifecycleSubject;
  readonly onOpenSubject: (subjectKey: string) => void;
}): ReactElement {
  const label = labelFor(props.subject);
  return (
    <article
      data-testid="canvas-focus-card"
      data-subject-kind={props.subject.kind}
      style={cardStyle}
    >
      <p style={eyebrowStyle}>{label}</p>
      <h2 style={titleStyle}>{props.subject.title}</h2>
      <button
        type="button"
        onClick={() => props.onOpenSubject(props.subject.key)}
        style={buttonStyle}
      >
        Open in Studio
      </button>
    </article>
  );
}

function GateFocusCard(): ReactElement {
  return (
    <article data-testid="canvas-focus-gate-card" style={cardStyle}>
      <p style={eyebrowStyle}>ACCESS NEEDED</p>
      <h2 style={titleStyle}>A connection is waiting</h2>
      <p style={copyStyle}>
        Open Studio to connect or choose an access policy.
      </p>
    </article>
  );
}

function labelFor(subject: CanvasLifecycleSubject): string {
  switch (subject.kind) {
    case "artifact":
      return "ARTIFACT";
    case "surface":
      return "RESULT";
    case "effect":
      return "PROPOSED CHANGE";
    case "receipt":
      return "RUN RECEIPT";
  }
}

const stackStyle: CSSProperties = {
  display: "grid",
  gap: "var(--space-3, 12px)",
  marginBottom: "var(--space-3, 12px)",
};
const cardStyle: CSSProperties = {
  display: "grid",
  gap: "var(--space-2, 8px)",
  padding: "var(--space-4, 16px)",
  border: "1px solid var(--color-border, #30343d)",
  borderRadius: "var(--radius-md, 8px)",
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
const buttonStyle: CSSProperties = {
  justifySelf: "start",
  border: "1px solid var(--color-border, #30343d)",
  borderRadius: "var(--radius-sm, 6px)",
  padding: "var(--space-2, 8px) var(--space-3, 12px)",
  color: "var(--color-text, #f4f5f6)",
  background: "var(--color-surface-raised, #202530)",
  cursor: "pointer",
};
