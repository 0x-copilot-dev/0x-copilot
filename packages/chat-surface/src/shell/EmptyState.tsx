// <EmptyState icon title body action? /> — one source of truth for the
// "nothing here yet" panel every destination renders.
//
// Source: cross-audit.md §1.6 + destinations-master-prd §4.1. The
// `role="status"` makes the empty state announce to assistive tech.

import type { CSSProperties, ReactElement, ReactNode } from "react";

export interface EmptyStateAction {
  readonly label: string;
  readonly onClick: () => void;
  readonly disabled?: boolean;
}

export interface EmptyStateProps {
  /** Pre-rendered icon (typically an SVG or emoji). Optional. */
  readonly icon?: ReactNode;
  readonly title: string;
  readonly body?: string;
  readonly action?: EmptyStateAction;
  readonly className?: string;
}

const wrapperStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  textAlign: "center",
  gap: 8,
  padding: 48,
  border: "1px dashed var(--color-border-strong, #2a2a2c)",
  borderRadius: "var(--radius-md, 12px)",
  color: "var(--color-text, #ededee)",
};

const iconStyle: CSSProperties = {
  marginBottom: 4,
  color: "var(--color-text-subtle, #7e7e84)",
  display: "inline-flex",
};

const titleStyle: CSSProperties = {
  fontSize: "var(--font-size-lg, 16px)",
  fontWeight: 600,
  margin: 0,
};

const bodyStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text-muted, #b4b4b8)",
  maxWidth: 420,
};

const actionStyle: CSSProperties = {
  marginTop: 8,
  height: 32,
  padding: "0 14px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border-strong, #2a2a2c)",
  backgroundColor: "transparent",
  color: "var(--color-accent, #d97757)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
};

export function EmptyState({
  icon,
  title,
  body,
  action,
  className,
}: EmptyStateProps): ReactElement {
  const disabled = action?.disabled === true;
  return (
    <div
      role="status"
      style={wrapperStyle}
      className={className}
      data-testid="empty-state"
    >
      {icon !== undefined ? (
        <div style={iconStyle} aria-hidden="true">
          {icon}
        </div>
      ) : null}
      <h3 style={titleStyle} data-testid="empty-state-title">
        {title}
      </h3>
      {body !== undefined && body.length > 0 ? (
        <div style={bodyStyle} data-testid="empty-state-body">
          {body}
        </div>
      ) : null}
      {action !== undefined ? (
        <button
          type="button"
          onClick={action.onClick}
          disabled={disabled}
          aria-disabled={disabled}
          style={{
            ...actionStyle,
            opacity: disabled ? 0.6 : 1,
            cursor: disabled ? "not-allowed" : "pointer",
          }}
          data-testid="empty-state-action"
        >
          {action.label}
        </button>
      ) : null}
    </div>
  );
}
