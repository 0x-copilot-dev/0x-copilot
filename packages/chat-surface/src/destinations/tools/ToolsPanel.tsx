// Tools — left-rail context panel (P10-B1).
//
// Per tools-prd §7.2:
//
//   - filter chips (kind / scope / status)
//   - search box
//   - "Onboard" CTA
//
// The destination owns its primary axis tabs at the top of the main pane;
// this panel is the SECONDARY filter surface (chips that AND with the
// active tab). Pure presentation — callback-driven.

import { type CSSProperties, type ReactElement, type ReactNode } from "react";

import { ContextPanel } from "../../shell/ContextPanel";

import {
  TOOLS_KIND_LABELS,
  TOOLS_KIND_ORDER,
  TOOLS_SCOPE_LABELS,
  TOOLS_SCOPE_ORDER,
  TOOLS_STATUS_LABELS,
  type ToolKind,
  type ToolScope,
  type ToolStatus,
} from "./_tools-stub";

const BORDER = "var(--color-border)";
const ACCENT = "var(--color-accent)";
const TEXT_PRIMARY = "var(--color-text)";
const TEXT_SECONDARY = "var(--color-text-muted)";
const TEXT_FAINT = "var(--color-text-subtle)";

const STATUS_ORDER: ReadonlyArray<ToolStatus> = [
  "enabled",
  "disabled",
  "error",
  "pending_review",
];

export interface ToolsPanelProps {
  /** Active kind chip. Null = all kinds. */
  readonly kindFilter?: ToolKind | null;
  readonly onKindFilterChange?: (next: ToolKind | null) => void;

  /** Active scope chip. Null = all scopes. */
  readonly scopeFilter?: ToolScope | null;
  readonly onScopeFilterChange?: (next: ToolScope | null) => void;

  /** Active status chip. Null = all statuses. */
  readonly statusFilter?: ToolStatus | null;
  readonly onStatusFilterChange?: (next: ToolStatus | null) => void;

  /** Search query (mirrors the destination's). */
  readonly search?: string;
  readonly onSearchChange?: (next: string) => void;

  /** "Onboard" CTA — opens the wizard. */
  readonly onOnboard?: () => void;

  /** Optional footer slot. */
  readonly footer?: ReactNode;
}

export function ToolsPanel(props: ToolsPanelProps = {}): ReactElement {
  const {
    kindFilter = null,
    onKindFilterChange,
    scopeFilter = null,
    onScopeFilterChange,
    statusFilter = null,
    onStatusFilterChange,
    search = "",
    onSearchChange,
    onOnboard,
    footer,
  } = props;

  const handleKind = (next: ToolKind | null): void => {
    if (onKindFilterChange !== undefined) onKindFilterChange(next);
  };
  const handleScope = (next: ToolScope | null): void => {
    if (onScopeFilterChange !== undefined) onScopeFilterChange(next);
  };
  const handleStatus = (next: ToolStatus | null): void => {
    if (onStatusFilterChange !== undefined) onStatusFilterChange(next);
  };

  return (
    <ContextPanel
      title="Tools"
      subtitle="Catalog filters"
      destination="tools"
      search={
        onSearchChange !== undefined
          ? {
              value: search,
              onChange: onSearchChange,
              placeholder: "Search tools",
            }
          : undefined
      }
      primaryAction={
        onOnboard !== undefined
          ? { label: "Onboard tool", onClick: onOnboard }
          : undefined
      }
      footer={footer}
    >
      <div data-testid="tools-panel">
        <PanelSectionWrapper title="Kind" testId="tools-panel-section-kind">
          <ChipRow ariaLabel="Tool kind filter">
            <ChipButton
              label="All"
              active={kindFilter === null}
              onClick={() => handleKind(null)}
              testId="tools-panel-kind-all"
            />
            {TOOLS_KIND_ORDER.map((kind) => (
              <ChipButton
                key={kind}
                label={TOOLS_KIND_LABELS[kind]}
                active={kindFilter === kind}
                onClick={() => handleKind(kindFilter === kind ? null : kind)}
                testId={`tools-panel-kind-${kind}`}
              />
            ))}
          </ChipRow>
        </PanelSectionWrapper>

        <PanelSectionWrapper title="Scope" testId="tools-panel-section-scope">
          <ChipRow ariaLabel="Tool scope filter">
            <ChipButton
              label="All"
              active={scopeFilter === null}
              onClick={() => handleScope(null)}
              testId="tools-panel-scope-all"
            />
            {TOOLS_SCOPE_ORDER.map((scope) => (
              <ChipButton
                key={scope}
                label={TOOLS_SCOPE_LABELS[scope]}
                active={scopeFilter === scope}
                onClick={() =>
                  handleScope(scopeFilter === scope ? null : scope)
                }
                testId={`tools-panel-scope-${scope}`}
              />
            ))}
          </ChipRow>
        </PanelSectionWrapper>

        <PanelSectionWrapper title="Status" testId="tools-panel-section-status">
          <ChipRow ariaLabel="Tool status filter">
            <ChipButton
              label="All"
              active={statusFilter === null}
              onClick={() => handleStatus(null)}
              testId="tools-panel-status-all"
            />
            {STATUS_ORDER.map((status) => (
              <ChipButton
                key={status}
                label={TOOLS_STATUS_LABELS[status]}
                active={statusFilter === status}
                onClick={() =>
                  handleStatus(statusFilter === status ? null : status)
                }
                testId={`tools-panel-status-${status}`}
              />
            ))}
          </ChipRow>
        </PanelSectionWrapper>
      </div>
    </ContextPanel>
  );
}

// ===========================================================================
// Internal — small panel section + chip primitives (panel-local only)
// ===========================================================================

interface PanelSectionWrapperProps {
  readonly title: string;
  readonly testId: string;
  readonly children: ReactNode;
}

function PanelSectionWrapper({
  title,
  testId,
  children,
}: PanelSectionWrapperProps): ReactElement {
  const wrapperStyle: CSSProperties = {
    padding: "12px 14px",
    borderBottom: `1px solid ${BORDER}`,
    display: "flex",
    flexDirection: "column",
    gap: 8,
  };
  const titleStyle: CSSProperties = {
    fontSize: "var(--font-size-xs, 12px)",
    fontWeight: 600,
    color: TEXT_FAINT,
    margin: 0,
    textTransform: "uppercase",
    letterSpacing: 0.4,
  };
  return (
    <section style={wrapperStyle} data-testid={testId}>
      <h3 style={titleStyle}>{title}</h3>
      {children}
    </section>
  );
}

interface ChipRowProps {
  readonly ariaLabel: string;
  readonly children: ReactNode;
}

function ChipRow({ ariaLabel, children }: ChipRowProps): ReactElement {
  const style: CSSProperties = {
    display: "flex",
    flexWrap: "wrap",
    gap: 6,
  };
  return (
    <div role="group" aria-label={ariaLabel} style={style}>
      {children}
    </div>
  );
}

interface ChipButtonProps {
  readonly label: string;
  readonly active: boolean;
  readonly onClick: () => void;
  readonly testId: string;
}

function ChipButton({
  label,
  active,
  onClick,
  testId,
}: ChipButtonProps): ReactElement {
  const style: CSSProperties = {
    padding: "3px 9px",
    borderRadius: "var(--radius-full, 999px)",
    fontSize: "var(--font-size-xs, 12px)",
    fontWeight: 500,
    border: `1px solid ${active ? ACCENT : BORDER}`,
    background: active
      ? "color-mix(in srgb, var(--color-accent) 12%, transparent)"
      : "transparent",
    color: active ? ACCENT : TEXT_PRIMARY,
    cursor: "pointer",
    fontFamily: "inherit",
  };
  return (
    <button
      type="button"
      onClick={onClick}
      style={style}
      data-testid={testId}
      data-active={active ? "true" : "false"}
      aria-pressed={active}
    >
      {label}
    </button>
  );
}

// Silence unused-variable lint for the secondary token (kept for cohesion
// with the rest of chat-surface; future panel sections will use it).
void TEXT_SECONDARY;
