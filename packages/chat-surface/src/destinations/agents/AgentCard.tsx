import { Badge, Button } from "@enterprise-search/design-system";
import {
  type CSSProperties,
  type KeyboardEvent,
  type MouseEvent,
  type ReactElement,
} from "react";

import { AGENT_COST_LABELS, type AgentStub } from "./_agents-stub";

// Design tokens — same approach as the other destinations. Names are kept
// for readability; values are CSS variables so theme/accent changes flow
// through automatically.
const BORDER = "var(--color-border)";
const TEXT_PRIMARY = "var(--color-text)";
const TEXT_SECONDARY = "var(--color-text-muted)";
const SURFACE = "var(--color-bg-elevated)";

export interface AgentCardProps {
  readonly agent: AgentStub;
  /**
   * Toggle install state. Callback-driven so the card stays pure
   * presentation — the destination owns the optimistic update.
   */
  readonly onToggleInstall: (agent: AgentStub) => void;
  /**
   * Open the detail panel for this agent. Detail is a P8-B2 surface;
   * the card only fires the callback.
   */
  readonly onViewDetails: (agent: AgentStub) => void;
}

/**
 * SP-1 card primitive for the Agents gallery.
 *
 * UI/UX preamble: App-Store / VSCode-extension gallery feel — icon, name,
 * one-line description, install button, cost chip. Cost chip is prominent
 * because trust depends on the user knowing what they will be charged
 * before they install anything.
 *
 * Behavior:
 *   - Whole card is a keyboard-activatable region (Enter / Space → details)
 *   - Install / Uninstall is a separate button; clicks stop propagation so
 *     they don't also trigger View Details.
 *   - View Details is the keyboard-default action — the most common
 *     navigation from a gallery card.
 *
 * Pure presentation: no transport, no router, no fetching. Callback-driven.
 */
export function AgentCard({
  agent,
  onToggleInstall,
  onViewDetails,
}: AgentCardProps): ReactElement {
  const handleCardClick = (): void => {
    onViewDetails(agent);
  };

  const handleCardKey = (e: KeyboardEvent<HTMLDivElement>): void => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onViewDetails(agent);
    }
  };

  const stopAndToggle = (e: MouseEvent<HTMLButtonElement>): void => {
    e.stopPropagation();
    onToggleInstall(agent);
  };

  const stopAndOpen = (e: MouseEvent<HTMLButtonElement>): void => {
    e.stopPropagation();
    onViewDetails(agent);
  };

  const cardStyle: CSSProperties = {
    padding: 16,
    backgroundColor: SURFACE,
    border: `1px solid ${BORDER}`,
    borderRadius: 10,
    display: "flex",
    flexDirection: "column",
    gap: 10,
    cursor: "pointer",
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
    borderRadius: 8,
    backgroundColor: "var(--color-surface)",
    border: `1px solid ${BORDER}`,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: 18,
    flexShrink: 0,
  };
  const titleColStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 2,
    minWidth: 0,
    flex: 1,
  };
  const nameStyle: CSSProperties = {
    fontSize: 14,
    fontWeight: 600,
    color: TEXT_PRIMARY,
    margin: 0,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const descStyle: CSSProperties = {
    fontSize: 13,
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
  };
  const buttonsRowStyle: CSSProperties = {
    display: "flex",
    gap: 6,
  };

  // Cost chip — uses design-system Badge (SP-1 chip pattern). Tone is
  // intentional: free reads "success" (green-ish), per-use reads "warning"
  // (drawing the eye because pay-as-you-go variance matters), the others
  // are neutral.
  const costTone: "neutral" | "success" | "warning" =
    agent.costTier === "free"
      ? "success"
      : agent.costTier === "per_use"
        ? "warning"
        : "neutral";

  return (
    <div
      role="listitem"
      tabIndex={0}
      data-testid="agent-card"
      data-agent-id={agent.id}
      data-agent-installed={agent.installed ? "true" : "false"}
      data-agent-origin={agent.origin}
      data-agent-cost={agent.costTier}
      onClick={handleCardClick}
      onKeyDown={handleCardKey}
      style={cardStyle}
      aria-label={`Agent ${agent.name}`}
    >
      <div style={headerStyle}>
        <div style={iconStyle} aria-hidden="true" data-testid="agent-card-icon">
          {agent.icon ?? agent.name.charAt(0).toUpperCase()}
        </div>
        <div style={titleColStyle}>
          <h3 style={nameStyle} data-testid="agent-card-name">
            {agent.name}
          </h3>
          <Badge
            tone={costTone}
            data-testid="agent-card-cost"
            data-cost-tier={agent.costTier}
          >
            {AGENT_COST_LABELS[agent.costTier]}
          </Badge>
        </div>
      </div>
      <p style={descStyle} data-testid="agent-card-description">
        {agent.description}
      </p>
      <div style={footerStyle}>
        <Button
          variant="secondary"
          size="sm"
          onClick={stopAndOpen}
          data-testid="agent-card-view-details"
        >
          View details
        </Button>
        <div style={buttonsRowStyle}>
          {agent.installed ? (
            <Button
              variant="secondary"
              size="sm"
              onClick={stopAndToggle}
              data-testid="agent-card-uninstall"
              aria-label={`Uninstall ${agent.name}`}
            >
              Uninstall
            </Button>
          ) : (
            <Button
              variant="primary"
              size="sm"
              onClick={stopAndToggle}
              data-testid="agent-card-install"
              aria-label={`Install ${agent.name}`}
            >
              Install
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
