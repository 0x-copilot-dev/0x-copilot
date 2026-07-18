// Tools — card primitive (P10-B1).
//
// One catalog row per `Tool` (tools-prd §7.2). The card carries:
//
//   - icon + name (truncated)
//   - kind chip (Built-in / MCP / API / Code / Skill)
//   - scope chip (Read / Write / Read+Write)
//   - <StatusPill> (one tone per `ToolStatus` — see `statusTone()`)
//   - 30-day call count + last-used relative time
//   - one-line description (clamped to 2 lines)
//
// Pure presentation: no transport, no router, no fetching. Click +
// keyboard Enter / Space fire `onOpen(tool)`. The destination (or any
// host) wires those into a `<ItemLink>` navigate or detail-pane open.

import {
  type CSSProperties,
  type KeyboardEvent,
  type MouseEvent,
  type ReactElement,
} from "react";

import { StatusPill } from "../../shell/StatusPill";
import { formatRelativeTime } from "../../util/time";

import {
  TOOLS_KIND_LABELS,
  TOOLS_SCOPE_LABELS,
  TOOLS_STATUS_LABELS,
  statusTone,
  type Tool,
} from "./_tools-stub";

const BORDER = "var(--color-border)";
const TEXT_PRIMARY = "var(--color-text)";
const TEXT_SECONDARY = "var(--color-text-muted)";
const TEXT_FAINT = "var(--color-text-subtle)";
const SURFACE = "var(--color-bg-elevated)";

export interface ToolCardProps {
  readonly tool: Tool;
  /**
   * Open this tool (detail view, popover, …). The card is purely
   * presentational — the host decides what "open" means.
   */
  readonly onOpen?: (tool: Tool) => void;
  /** Reference instant for relative-time formatting (test seam). */
  readonly now?: number;
}

export function ToolCard({ tool, onOpen, now }: ToolCardProps): ReactElement {
  const handleClick = (): void => {
    if (onOpen !== undefined) onOpen(tool);
  };
  const handleKey = (e: KeyboardEvent<HTMLDivElement>): void => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (onOpen !== undefined) onOpen(tool);
    }
  };
  const stopOpen = (e: MouseEvent<HTMLDivElement>): void => {
    // The chips inside the card are not interactive but stopping
    // propagation here would block keyboard Enter — leave it alone.
    void e;
  };

  const cardStyle: CSSProperties = {
    padding: 16,
    backgroundColor: SURFACE,
    border: `1px solid ${BORDER}`,
    borderRadius: "var(--radius-md, 10px)",
    display: "flex",
    flexDirection: "column",
    gap: 10,
    cursor: onOpen !== undefined ? "pointer" : "default",
    minHeight: 156,
    boxSizing: "border-box",
    outline: "none",
  };
  const headerStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 10,
  };
  const iconStyle: CSSProperties = {
    width: 36,
    height: 36,
    borderRadius: "var(--radius-sm, 8px)",
    backgroundColor: "var(--color-surface, #16161a)",
    border: `1px solid ${BORDER}`,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: "var(--font-size-xs)",
    fontWeight: 600,
    color: TEXT_SECONDARY,
    flexShrink: 0,
    letterSpacing: 0.5,
    textTransform: "uppercase",
  };
  const titleColStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 4,
    minWidth: 0,
    flex: 1,
  };
  const nameStyle: CSSProperties = {
    fontSize: "var(--font-size-sm, 14px)",
    fontWeight: 600,
    color: TEXT_PRIMARY,
    margin: 0,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const chipsRowStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 6,
    flexWrap: "wrap",
  };
  const chipStyle: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    height: 18,
    padding: "0 8px",
    borderRadius: "var(--radius-full, 999px)",
    backgroundColor: "var(--color-surface-muted, #222224)",
    color: TEXT_SECONDARY,
    fontSize: "var(--font-size-2xs, 11px)",
    fontWeight: 600,
    letterSpacing: 0.3,
    textTransform: "uppercase",
    border: `1px solid ${BORDER}`,
    whiteSpace: "nowrap",
  };
  const descStyle: CSSProperties = {
    fontSize: "var(--font-size-sm, 13px)",
    color: TEXT_SECONDARY,
    margin: 0,
    display: "-webkit-box",
    WebkitLineClamp: 2,
    WebkitBoxOrient: "vertical",
    overflow: "hidden",
    flex: 1,
  };
  const footerStyle: CSSProperties = {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 8,
    fontSize: "var(--font-size-xs, 12px)",
    color: TEXT_FAINT,
  };

  const calls = tool.usage.calls_30d;
  const lastUsed =
    tool.usage.last_used_at !== null
      ? formatRelativeTime(tool.usage.last_used_at, now)
      : "never";

  return (
    <div
      role="listitem"
      tabIndex={onOpen !== undefined ? 0 : -1}
      data-testid="tool-card"
      data-tool-id={tool.id}
      data-tool-kind={tool.kind}
      data-tool-scope={tool.scope}
      data-tool-status={tool.status}
      onClick={onOpen !== undefined ? handleClick : undefined}
      onKeyDown={onOpen !== undefined ? handleKey : undefined}
      style={cardStyle}
      aria-label={`Tool ${tool.name}`}
    >
      <div style={headerStyle}>
        <div style={iconStyle} aria-hidden="true" data-testid="tool-card-icon">
          {TOOLS_KIND_LABELS[tool.kind].slice(0, 3)}
        </div>
        <div style={titleColStyle}>
          <h3 style={nameStyle} data-testid="tool-card-name" title={tool.name}>
            {tool.name}
          </h3>
          <div style={chipsRowStyle} data-testid="tool-card-chips">
            <span
              style={chipStyle}
              data-testid="tool-card-kind"
              data-tool-kind={tool.kind}
            >
              {TOOLS_KIND_LABELS[tool.kind]}
            </span>
            <span
              style={chipStyle}
              data-testid="tool-card-scope"
              data-tool-scope={tool.scope}
            >
              {TOOLS_SCOPE_LABELS[tool.scope]}
            </span>
            <StatusPill
              status={statusTone(tool.status)}
              label={TOOLS_STATUS_LABELS[tool.status]}
            />
          </div>
        </div>
      </div>
      <p
        style={descStyle}
        data-testid="tool-card-description"
        onClick={stopOpen}
      >
        {tool.description.length > 0
          ? tool.description
          : "No description provided."}
      </p>
      <div style={footerStyle} data-testid="tool-card-footer">
        <span data-testid="tool-card-calls">
          {calls.toLocaleString()} call{calls === 1 ? "" : "s"} · 30d
        </span>
        <span data-testid="tool-card-last-used">Last used {lastUsed}</span>
      </div>
    </div>
  );
}
