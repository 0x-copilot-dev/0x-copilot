// `OnboardingWizard` — entry for `/tools/onboard` per tools-prd §7.1.
//
// Renders a kind picker (4 cards: OpenAPI / MCP / Code / Skill). Once the
// user picks a kind, the sub-wizard takes over. Hosts wire each kind's
// callbacks (`onFetchOpenApi`, `onTestCall`, `onStartOAuth`,
// `onSkillContinue`) and `onSave` for each branch — this file is the
// presentation shell only.
//
// DRY: every sub-wizard already owns its step machine + ARIA wiring.
// This shell does not duplicate any of that.

import {
  useCallback,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import { Button } from "@enterprise-search/design-system";
import type { ToolKind } from "@enterprise-search/api-types";

import {
  CodeWizard,
  McpWizard,
  OpenApiWizard,
  SkillWizard,
  type CodeWizardProps,
  type McpWizardProps,
  type OpenApiWizardProps,
  type SkillWizardProps,
} from "./onboarding";

/** The four onboarding kinds. `builtin` is not user-onboarded. */
type OnboardableKind = Exclude<ToolKind, "builtin">;

export interface OnboardingWizardProps {
  /**
   * Initial kind to render. When undefined the shell shows the kind
   * picker. When set the shell jumps straight to that sub-wizard so
   * routes like `/tools/onboard/openapi` skip the picker.
   */
  readonly initialKind?: OnboardableKind;
  readonly openapi: Omit<OpenApiWizardProps, "onCancel">;
  readonly mcp: Omit<McpWizardProps, "onCancel">;
  readonly code: Omit<CodeWizardProps, "onCancel">;
  readonly skill: Omit<SkillWizardProps, "onCancel">;
  /** Called when the user cancels (used by the back-to-picker affordance too). */
  readonly onCancel?: () => void;
}

const KIND_CARDS: ReadonlyArray<{
  readonly kind: OnboardableKind;
  readonly title: string;
  readonly description: string;
  readonly badge: string;
}> = [
  {
    kind: "openapi",
    title: "OpenAPI",
    description:
      "Paste an OpenAPI document URL. Pick operations, choose auth, test the call.",
    badge: "REST",
  },
  {
    kind: "mcp",
    title: "MCP server",
    description:
      "Install an MCP server from the marketplace or a custom URL. OAuth-then-discover.",
    badge: "MCP",
  },
  {
    kind: "code",
    title: "Code routine",
    description:
      "Author deterministic Python. Sandboxed; allow-list enforced server-side.",
    badge: "PY",
  },
  {
    kind: "skill",
    title: "Skill",
    description:
      "Skills live in Library. We&apos;ll deep-link you to the Library editor.",
    badge: "LIB",
  },
];

export function OnboardingWizard(props: OnboardingWizardProps): ReactElement {
  const { initialKind, openapi, mcp, code, skill, onCancel } = props;

  const [picked, setPicked] = useState<OnboardableKind | null>(
    initialKind ?? null,
  );

  const backToPicker = useCallback(() => setPicked(null), []);

  if (picked === null) {
    return (
      <div data-testid="tools-onboarding-wizard" style={containerStyle}>
        <header style={headerStyle}>
          <h2 style={titleStyle}>Onboard a tool</h2>
          <p style={subtitleStyle}>
            Pick the kind you&apos;re registering. Each path opens a dedicated
            wizard.
          </p>
        </header>
        <section aria-labelledby="step-kind-picker" style={bodyStyle}>
          <h3 id="step-kind-picker" style={stepTitleStyle}>
            Choose a kind
          </h3>
          <div
            role="list"
            aria-label="Onboarding kinds"
            style={cardGridStyle}
            data-testid="tools-onboarding-kinds"
          >
            {KIND_CARDS.map((card) => (
              <button
                key={card.kind}
                type="button"
                role="listitem"
                onClick={() => setPicked(card.kind)}
                style={cardStyle}
                data-testid={`tools-onboarding-kind-${card.kind}`}
              >
                <span style={badgeStyle}>{card.badge}</span>
                <span style={cardTitleStyle}>{card.title}</span>
                <span style={cardDescStyle}>{card.description}</span>
              </button>
            ))}
          </div>
          {onCancel !== undefined ? (
            <div style={cancelRowStyle}>
              <Button
                variant="ghost"
                size="md"
                onClick={onCancel}
                data-testid="tools-onboarding-cancel"
              >
                Cancel
              </Button>
            </div>
          ) : null}
        </section>
      </div>
    );
  }

  // Sub-wizard branches. We pass `onCancel = backToPicker` so the user
  // can step out of a sub-wizard back to the kind picker.
  if (picked === "openapi") {
    return <OpenApiWizard {...openapi} onCancel={backToPicker} />;
  }
  if (picked === "mcp") {
    return <McpWizard {...mcp} onCancel={backToPicker} />;
  }
  if (picked === "code") {
    return <CodeWizard {...code} onCancel={backToPicker} />;
  }
  // picked === "skill"
  return <SkillWizard {...skill} onCancel={backToPicker} />;
}

// ---------------------------------------------------------------------------
// Styles.
// ---------------------------------------------------------------------------

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

const bodyStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 10,
  paddingTop: 8,
  borderTop: "1px solid var(--color-border)",
};

const stepTitleStyle: CSSProperties = {
  margin: 0,
  fontSize: 14,
  fontWeight: 600,
};

const cardGridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
  gap: 12,
};

const cardStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "flex-start",
  gap: 8,
  padding: 14,
  textAlign: "left",
  background: "var(--color-bg-elevated)",
  color: "var(--color-text)",
  border: "1px solid var(--color-border)",
  borderRadius: 8,
  cursor: "pointer",
  fontFamily: "inherit",
};

const badgeStyle: CSSProperties = {
  display: "inline-block",
  padding: "2px 6px",
  borderRadius: 999,
  background: "var(--color-bg)",
  color: "var(--color-text-muted)",
  fontSize: 10,
  fontWeight: 700,
  letterSpacing: 0.4,
};

const cardTitleStyle: CSSProperties = {
  fontSize: 14,
  fontWeight: 600,
};

const cardDescStyle: CSSProperties = {
  fontSize: 12.5,
  color: "var(--color-text-muted)",
};

const cancelRowStyle: CSSProperties = {
  display: "flex",
  justifyContent: "flex-end",
  paddingTop: 8,
};
