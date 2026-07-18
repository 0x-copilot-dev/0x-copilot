import { type CSSProperties, type ReactElement, type ReactNode } from "react";

const PANEL_WIDTH = 224;

export interface ContextPanelPrimaryAction {
  readonly label: string;
  readonly onClick: () => void;
}

export interface ContextPanelSearch {
  readonly value: string;
  readonly onChange: (next: string) => void;
  readonly placeholder?: string;
}

export interface ContextPanelProps {
  /**
   * Section title rendered in the header. Always shown — the
   * ContextPanel is meant to be a labelled column. If the host wants
   * a panel-less destination (e.g. chats), it omits the ContextPanel
   * entirely rather than passing a null title.
   */
  readonly title: string;
  readonly subtitle?: string;
  readonly search?: ContextPanelSearch;
  readonly primaryAction?: ContextPanelPrimaryAction;
  /** Body content — destination-specific lists / sections live here. */
  readonly children?: ReactNode;
  /** Optional footer (counts, last-sync-at, etc.). */
  readonly footer?: ReactNode;
  /** Identifies the rendered panel for tests + theming hooks. */
  readonly destination?: string;
}

/**
 * Generic 224-wide left-of-main panel. Mirrors the prototype's
 * `<ContextPanel>` shell (os-shell.jsx) — head (title / subtitle /
 * search / primary action) + scrollable body + optional footer. Per
 * destination panel content lives in the host (apps/frontend) so the
 * shell stays a layout primitive, not a registry.
 */
export function ContextPanel({
  title,
  subtitle,
  search,
  primaryAction,
  children,
  footer,
  destination,
}: ContextPanelProps): ReactElement {
  const panelStyle: CSSProperties = {
    width: PANEL_WIDTH,
    minWidth: PANEL_WIDTH,
    height: "100%",
    backgroundColor: "var(--color-bg-elevated)",
    borderRight: "1px solid var(--color-border)",
    color: "var(--color-text)",
    display: "flex",
    flexDirection: "column",
    boxSizing: "border-box",
  };
  const headStyle: CSSProperties = {
    padding: "16px 14px 12px",
    borderBottom: "1px solid var(--color-border)",
    display: "flex",
    flexDirection: "column",
    gap: 8,
  };
  const titleStyle: CSSProperties = {
    fontSize: "var(--font-size-sm)",
    fontWeight: 600,
    letterSpacing: 0.2,
    color: "var(--color-text)",
  };
  const subtitleStyle: CSSProperties = {
    fontSize: "var(--font-size-2xs)",
    color: "var(--color-text-subtle)",
  };
  const searchWrapStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 6,
    background: "var(--color-surface)",
    border: "1px solid var(--color-border)",
    borderRadius: 7,
    padding: "5px 10px",
  };
  const searchInputStyle: CSSProperties = {
    flex: 1,
    border: "none",
    background: "transparent",
    color: "var(--color-text)",
    fontSize: "var(--font-size-xs)",
    outline: "none",
    minWidth: 0,
  };
  const primaryActionStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    padding: "6px 10px",
    background: "transparent",
    color: "var(--color-text)",
    border: "1px solid var(--color-border)",
    borderRadius: 6,
    fontSize: "var(--font-size-xs)",
    fontWeight: 500,
    cursor: "pointer",
  };
  const bodyStyle: CSSProperties = {
    flex: 1,
    overflowY: "auto",
    padding: "8px 0",
    minHeight: 0,
  };
  const footerStyle: CSSProperties = {
    borderTop: "1px solid var(--color-border)",
    padding: "8px 14px",
    fontSize: "var(--font-size-2xs)",
    color: "var(--color-text-muted)",
  };

  return (
    <aside
      aria-label={`${title} panel`}
      style={panelStyle}
      data-component="context-panel"
      data-destination={destination ?? undefined}
    >
      <div style={headStyle}>
        <div style={titleStyle} data-testid="context-panel-header">
          {title}
        </div>
        {subtitle !== undefined && subtitle !== "" ? (
          <div style={subtitleStyle} data-testid="context-panel-subtitle">
            {subtitle}
          </div>
        ) : null}
        {search ? (
          <div style={searchWrapStyle}>
            <SearchIcon />
            <input
              data-testid="context-panel-search"
              value={search.value}
              onChange={(e) => search.onChange(e.target.value)}
              placeholder={search.placeholder ?? "Search…"}
              style={searchInputStyle}
            />
          </div>
        ) : null}
        {primaryAction ? (
          <button
            type="button"
            data-testid="context-panel-primary-action"
            onClick={primaryAction.onClick}
            style={primaryActionStyle}
          >
            <PlusIcon />
            <span>{primaryAction.label}</span>
          </button>
        ) : null}
      </div>
      <div style={bodyStyle} data-testid="context-panel-body">
        {children ?? <EmptyState />}
      </div>
      {footer ? <div style={footerStyle}>{footer}</div> : null}
    </aside>
  );
}

function EmptyState(): ReactElement {
  return (
    <p
      style={{
        margin: 0,
        padding: "24px 14px",
        color: "var(--color-text-subtle)",
        fontSize: "var(--font-size-xs)",
        textAlign: "center",
      }}
      data-testid="context-panel-empty"
    >
      Nothing here yet.
    </p>
  );
}

function SearchIcon(): ReactElement {
  return (
    <svg
      aria-hidden="true"
      width={14}
      height={14}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ color: "var(--color-text-muted)", flexShrink: 0 }}
    >
      <circle cx="11" cy="11" r="7" />
      <path d="M21 21l-4.3-4.3" />
    </svg>
  );
}

function PlusIcon(): ReactElement {
  return (
    <svg
      aria-hidden="true"
      width={14}
      height={14}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

export { PANEL_WIDTH as CONTEXT_PANEL_WIDTH };
