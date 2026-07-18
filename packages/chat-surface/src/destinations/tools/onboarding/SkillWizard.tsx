// `SkillWizard` — minimal handoff to Library per tools-prd §10 Q9.
//
// Skills are first-class Library pages tagged `kind=skill`; the prompt
// + response template live in Library. The Tools wizard's skill branch
// is just a deep-link out to `/library/new?kind=skill`. Nothing else.
//
// Owning logic is intentionally tiny: a single call-to-action button
// plus the explanation for why we hand off to Library.

import type { CSSProperties, ReactElement } from "react";

import { Button } from "@0x-copilot/design-system";

export interface SkillWizardProps {
  /**
   * Continue handler — host navigates the app to the Library page editor
   * (`/library/new?kind=skill` per sub-PRD §7.1). Chat-surface does not
   * own routing.
   */
  readonly onSkillContinue: () => void;
  readonly onCancel?: () => void;
}

export function SkillWizard(props: SkillWizardProps): ReactElement {
  const { onSkillContinue, onCancel } = props;

  return (
    <div data-testid="skill-wizard" style={containerStyle}>
      <header style={headerStyle}>
        <h2 style={titleStyle}>Author a skill</h2>
        <p style={subtitleStyle}>
          Skills live in Library, not in Tools. The prompt and response template
          are versioned alongside other Library pages; Tools only carries the
          wire-callable shim that links back to the page.
        </p>
      </header>

      <section
        aria-labelledby="step-skill-continue"
        style={bodyStyle}
        data-testid="skill-wizard-body"
      >
        <h3 id="step-skill-continue" style={stepTitleStyle}>
          Continue to Library
        </h3>
        <p style={hintStyle}>
          You&apos;ll be taken to the Library page editor with{" "}
          <code>kind=skill</code> pre-selected. On save, a Tool row of kind{" "}
          <code>skill</code> is created automatically and back-links to the
          Library page.
        </p>
        <div style={ctaRowStyle}>
          <Button
            variant="primary"
            size="md"
            onClick={onSkillContinue}
            data-testid="skill-wizard-continue"
          >
            Continue to Library
          </Button>
          {onCancel !== undefined ? (
            <Button
              variant="ghost"
              size="md"
              onClick={onCancel}
              data-testid="skill-wizard-cancel"
            >
              Cancel
            </Button>
          ) : null}
        </div>
      </section>
    </div>
  );
}

const containerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 16,
  padding: 16,
  background: "var(--color-bg)",
  color: "var(--color-text)",
  border: "1px solid var(--color-border)",
  borderRadius: 10,
  boxSizing: "border-box",
};

const headerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
};

const titleStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-lg)",
  fontWeight: 600,
};

const subtitleStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm)",
  color: "var(--color-text-muted)",
};

const bodyStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 10,
  paddingTop: 8,
  borderTop: "1px solid var(--color-border)",
};

const stepTitleStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-md)",
  fontWeight: 600,
};

const hintStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm)",
  color: "var(--color-text-muted)",
};

const ctaRowStyle: CSSProperties = {
  display: "flex",
  gap: 8,
  alignItems: "center",
  marginTop: 8,
};
