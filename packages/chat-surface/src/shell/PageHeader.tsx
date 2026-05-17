// Every destination's main view starts with <PageHeader>. No destination
// renders its own header chrome.
//
// Source: cross-audit.md §1.6 (binding 2026-05-17). Props shape is the
// single source of truth.

import type { CSSProperties, ReactElement, ReactNode } from "react";

export interface PageHeaderPrimaryAction {
  readonly label: string;
  readonly onClick: () => void;
  readonly disabled?: boolean;
}

export interface PageHeaderProps {
  readonly title: string;
  readonly subtitle?: string;
  /** Pre-rendered badges (e.g. <StatusPill />, count chips). */
  readonly badges?: ReactNode;
  /** Right-aligned action buttons. Use the emphasised primary slot below
   *  for the single CTA when there is one. */
  readonly actions?: ReactNode;
  readonly primaryAction?: PageHeaderPrimaryAction;
}

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  paddingBottom: 12,
  borderBottom: "1px solid var(--color-border, #232325)",
};

const topRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  justifyContent: "space-between",
  gap: 16,
};

const titleBlockStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
  minWidth: 0,
};

const titleStyle: CSSProperties = {
  fontSize: "var(--font-size-2xl, 22px)",
  fontWeight: "var(--font-weight-semibold, 600)" as unknown as number,
  color: "var(--color-text, #ededee)",
  margin: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const subtitleStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text-muted, #b4b4b8)",
};

const actionsStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  flexShrink: 0,
};

const primaryButtonStyle: CSSProperties = {
  height: 32,
  padding: "0 14px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-accent, #d97757)",
  backgroundColor: "var(--color-accent, #d97757)",
  color: "var(--color-accent-contrast, #1a0f0a)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: "var(--font-weight-semibold, 600)" as unknown as number,
  cursor: "pointer",
};

const badgesRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  flexWrap: "wrap",
};

export function PageHeader({
  title,
  subtitle,
  badges,
  actions,
  primaryAction,
}: PageHeaderProps): ReactElement {
  const disabled = primaryAction?.disabled === true;
  return (
    <header
      role="region"
      aria-label={title}
      style={rootStyle}
      data-testid="page-header"
    >
      <div style={topRowStyle}>
        <div style={titleBlockStyle}>
          <h1 style={titleStyle} data-testid="page-header-title">
            {title}
          </h1>
          {subtitle !== undefined && subtitle.length > 0 ? (
            <div style={subtitleStyle} data-testid="page-header-subtitle">
              {subtitle}
            </div>
          ) : null}
        </div>
        <div style={actionsStyle}>
          {actions}
          {primaryAction !== undefined ? (
            <button
              type="button"
              onClick={primaryAction.onClick}
              disabled={disabled}
              aria-disabled={disabled}
              style={{
                ...primaryButtonStyle,
                opacity: disabled ? 0.6 : 1,
                cursor: disabled ? "not-allowed" : "pointer",
              }}
              data-testid="page-header-primary-action"
            >
              {primaryAction.label}
            </button>
          ) : null}
        </div>
      </div>
      {badges !== undefined ? (
        <div style={badgesRowStyle} data-testid="page-header-badges">
          {badges}
        </div>
      ) : null}
    </header>
  );
}
