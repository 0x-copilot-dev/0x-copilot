// `WizardShell` — shared chrome for every onboarding sub-wizard.
//
// Why a shell:
// - Sub-PRD §7.4 requires identical step navigation across the four
//   wizard branches. Pulling the stepper + back/next bar out keeps each
//   wizard tightly scoped to its own steps (DRY).
// - Acceptance bar requires:
//   * `<nav aria-label="Onboarding steps">` for the stepper, with the
//     current step marked `aria-current="step"`.
//   * each step rendered as `<section aria-labelledby="step-N">`.
//
// This file owns only chrome; per-step form state is owned by each wizard.

import type { CSSProperties, ReactElement, ReactNode } from "react";

import { Button } from "@0x-copilot/design-system";

export interface WizardStepDescriptor {
  /** Stable id used for ARIA wiring (`step-${id}`). */
  readonly id: string;
  readonly label: string;
}

export interface WizardShellProps {
  readonly steps: ReadonlyArray<WizardStepDescriptor>;
  readonly currentStep: number;
  readonly title: string;
  readonly subtitle?: string;
  readonly children: ReactNode;
  /** Back button handler. Hidden when undefined OR on the first step. */
  readonly onBack?: () => void;
  /** Next button handler. Hidden when undefined OR on the last step. */
  readonly onNext?: () => void;
  /** Final-step CTA handler ("Save" for OpenAPI/Code, "Enable" for MCP). */
  readonly onFinish?: () => void;
  readonly finishLabel?: string;
  /** When the parent wants to keep the user on the current step. */
  readonly nextDisabled?: boolean;
  readonly finishDisabled?: boolean;
  /** Extra slot in the footer (e.g. "Save anyway with status=error"). */
  readonly footerSlot?: ReactNode;
  readonly testIdPrefix: string;
}

export function WizardShell(props: WizardShellProps): ReactElement {
  const {
    steps,
    currentStep,
    title,
    subtitle,
    children,
    onBack,
    onNext,
    onFinish,
    finishLabel = "Save",
    nextDisabled = false,
    finishDisabled = false,
    footerSlot,
    testIdPrefix,
  } = props;

  const isLast = currentStep === steps.length - 1;
  const isFirst = currentStep === 0;

  const activeStep = steps[currentStep] ?? steps[0];

  return (
    <div
      style={containerStyle}
      data-testid={`${testIdPrefix}-wizard`}
      data-current-step={currentStep}
    >
      <header style={headerStyle}>
        <h2 style={titleStyle}>{title}</h2>
        {subtitle !== undefined ? (
          <p style={subtitleStyle}>{subtitle}</p>
        ) : null}
      </header>

      <nav aria-label="Onboarding steps" style={stepperStyle}>
        <ol style={stepperListStyle}>
          {steps.map((step, idx) => {
            const isCurrent = idx === currentStep;
            const done = idx < currentStep;
            return (
              <li
                key={step.id}
                style={stepperItemStyle(isCurrent, done)}
                aria-current={isCurrent ? "step" : undefined}
                data-testid={`${testIdPrefix}-stepper-${step.id}`}
              >
                <span
                  aria-hidden="true"
                  style={stepperIndexStyle(isCurrent, done)}
                >
                  {idx + 1}
                </span>
                <span>{step.label}</span>
              </li>
            );
          })}
        </ol>
      </nav>

      <section
        aria-labelledby={
          activeStep !== undefined ? `step-${activeStep.id}` : undefined
        }
        data-testid={`${testIdPrefix}-step-${activeStep?.id ?? "unknown"}`}
        style={bodyStyle}
      >
        <h3
          id={activeStep !== undefined ? `step-${activeStep.id}` : undefined}
          style={stepTitleStyle}
        >
          {activeStep?.label ?? ""}
        </h3>
        {children}
      </section>

      <footer style={footerStyle}>
        <div style={footerLeftStyle}>{footerSlot}</div>
        <div style={footerRightStyle}>
          {!isFirst && onBack !== undefined ? (
            <Button
              variant="secondary"
              size="md"
              onClick={onBack}
              data-testid={`${testIdPrefix}-back`}
            >
              Back
            </Button>
          ) : null}
          {!isLast && onNext !== undefined ? (
            <Button
              variant="primary"
              size="md"
              onClick={onNext}
              disabled={nextDisabled}
              data-testid={`${testIdPrefix}-next`}
            >
              Next
            </Button>
          ) : null}
          {isLast && onFinish !== undefined ? (
            <Button
              variant="primary"
              size="md"
              onClick={onFinish}
              disabled={finishDisabled}
              data-testid={`${testIdPrefix}-finish`}
            >
              {finishLabel}
            </Button>
          ) : null}
        </div>
      </footer>
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
  fontSize: 16,
  fontWeight: 600,
};

const subtitleStyle: CSSProperties = {
  margin: 0,
  fontSize: 13,
  color: "var(--color-text-muted)",
};

const stepperStyle: CSSProperties = {
  borderTop: "1px solid var(--color-border)",
  borderBottom: "1px solid var(--color-border)",
  padding: "10px 0",
};

const stepperListStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 8,
  margin: 0,
  padding: 0,
  listStyle: "none",
};

const stepperItemStyle = (
  isCurrent: boolean,
  done: boolean,
): CSSProperties => ({
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  padding: "4px 10px",
  fontSize: 12,
  fontWeight: isCurrent ? 600 : 400,
  color: isCurrent
    ? "var(--color-text)"
    : done
      ? "var(--color-text)"
      : "var(--color-text-muted)",
  border: `1px solid ${
    isCurrent ? "var(--color-accent)" : "var(--color-border)"
  }`,
  borderRadius: 999,
  background: isCurrent ? "var(--color-bg-elevated)" : "transparent",
});

const stepperIndexStyle = (
  isCurrent: boolean,
  done: boolean,
): CSSProperties => ({
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 18,
  height: 18,
  borderRadius: 999,
  fontSize: 11,
  fontWeight: 600,
  background:
    isCurrent || done ? "var(--color-accent)" : "var(--color-bg-elevated)",
  color: isCurrent || done ? "var(--color-bg)" : "var(--color-text-muted)",
});

const bodyStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
  padding: "4px 0",
};

const stepTitleStyle: CSSProperties = {
  margin: 0,
  fontSize: 14,
  fontWeight: 600,
};

const footerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  paddingTop: 10,
  borderTop: "1px solid var(--color-border)",
};

const footerLeftStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
};

const footerRightStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
};
