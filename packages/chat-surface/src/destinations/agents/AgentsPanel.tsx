import { type CSSProperties, type ReactElement, type ReactNode } from "react";

import {
  AGENT_FILTER_LABELS,
  type AgentFilter,
  type AgentOrigin,
} from "./_agents-stub";

// Tokens — same as the rest of chat-surface. Names are kept for readability;
// values are CSS variables so theme/accent flow through.
const BORDER = "var(--color-border)";
const TEXT_PRIMARY = "var(--color-text)";
const TEXT_SECONDARY = "var(--color-text-muted)";
const TEXT_FAINT = "var(--color-text-subtle)";
const ACCENT = "var(--color-accent)";
const SURFACE = "var(--color-bg-elevated)";

const PANEL_WIDTH = 224;

export interface AgentsPanelProps {
  /** Active filter — drives which axis row is highlighted. */
  readonly filter: AgentFilter;
  readonly onFilterChange: (next: AgentFilter) => void;
  /** Origin facet — drives the origin section. Optional; null = no filter. */
  readonly originFilter: AgentOrigin | null;
  readonly onOriginFilterChange: (next: AgentOrigin | null) => void;
  /** Skill facet — drives the by-skill chip row. */
  readonly skillFilter: string | null;
  readonly onSkillFilterChange: (next: string | null) => void;
  /** Connector facet — for "agents that touch X". */
  readonly connectorFilter: string | null;
  readonly onConnectorFilterChange: (next: string | null) => void;
  /** Distinct skill names extracted from the agent set — drives the chip row. */
  readonly skills: ReadonlyArray<string>;
  /** Distinct connector names — drives the connector chip row. */
  readonly connectors: ReadonlyArray<string>;
}

/**
 * Context panel for the Agents destination.
 *
 * Three facets per the master-prd §5.6: origin, skill, connector. The
 * destination owns its own search + filter-tabs at the top of the main
 * pane; this panel is the SECONDARY filter axis surface — collapsible
 * sections of chips that AND with whatever filter tab is active.
 *
 * Pure presentation: callback-driven. No transport.
 */
export function AgentsPanel({
  filter,
  onFilterChange,
  originFilter,
  onOriginFilterChange,
  skillFilter,
  onSkillFilterChange,
  connectorFilter,
  onConnectorFilterChange,
  skills,
  connectors,
}: AgentsPanelProps): ReactElement {
  const panelStyle: CSSProperties = {
    width: PANEL_WIDTH,
    minWidth: PANEL_WIDTH,
    height: "100%",
    backgroundColor: SURFACE,
    borderRight: `1px solid ${BORDER}`,
    color: TEXT_PRIMARY,
    display: "flex",
    flexDirection: "column",
    boxSizing: "border-box",
  };
  const headStyle: CSSProperties = {
    padding: "16px 14px 12px",
    borderBottom: `1px solid ${BORDER}`,
  };
  const headTitleStyle: CSSProperties = {
    fontSize: "var(--font-size-sm)",
    fontWeight: 600,
    letterSpacing: 0.2,
  };
  const bodyStyle: CSSProperties = {
    flex: 1,
    overflowY: "auto",
    padding: "10px 0",
    minHeight: 0,
  };

  return (
    <aside
      aria-label="Agents filters panel"
      data-component="agents-panel"
      data-testid="agents-panel"
      style={panelStyle}
    >
      <div style={headStyle}>
        <div style={headTitleStyle}>Filters</div>
      </div>
      <div style={bodyStyle}>
        <SectionGroup label="View">
          {(
            ["my", "installed", "available", "custom", "by_skill"] as const
          ).map((key) => (
            <FilterRow
              key={key}
              label={AGENT_FILTER_LABELS[key]}
              active={filter === key}
              onClick={() => onFilterChange(key)}
              testId={`agents-panel-filter-${key}`}
            />
          ))}
        </SectionGroup>

        <SectionGroup label="Origin">
          <FilterRow
            label="All origins"
            active={originFilter === null}
            onClick={() => onOriginFilterChange(null)}
            testId="agents-panel-origin-all"
          />
          {(["installed", "available", "custom"] as const).map((origin) => (
            <FilterRow
              key={origin}
              label={originLabel(origin)}
              active={originFilter === origin}
              onClick={() => onOriginFilterChange(origin)}
              testId={`agents-panel-origin-${origin}`}
            />
          ))}
        </SectionGroup>

        <SectionGroup label="Skill">
          {skills.length === 0 ? (
            <EmptyRow hint="No skills yet" />
          ) : (
            <div
              data-testid="agents-panel-skill-row"
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: 6,
                padding: "4px 14px",
              }}
            >
              {skills.map((skill) => (
                <ChipButton
                  key={skill}
                  label={skill}
                  active={skillFilter === skill}
                  onClick={() =>
                    onSkillFilterChange(skillFilter === skill ? null : skill)
                  }
                  testId={`agents-panel-skill-${skill}`}
                />
              ))}
            </div>
          )}
        </SectionGroup>

        <SectionGroup label="Connector">
          {connectors.length === 0 ? (
            <EmptyRow hint="No connectors yet" />
          ) : (
            <div
              data-testid="agents-panel-connector-row"
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: 6,
                padding: "4px 14px",
              }}
            >
              {connectors.map((c) => (
                <ChipButton
                  key={c}
                  label={c}
                  active={connectorFilter === c}
                  onClick={() =>
                    onConnectorFilterChange(connectorFilter === c ? null : c)
                  }
                  testId={`agents-panel-connector-${c}`}
                />
              ))}
            </div>
          )}
        </SectionGroup>
      </div>
    </aside>
  );
}

function originLabel(origin: AgentOrigin): string {
  if (origin === "installed") return "Installed";
  if (origin === "available") return "Marketplace";
  return "Custom";
}

interface SectionGroupProps {
  readonly label: string;
  readonly children: ReactNode;
}

function SectionGroup({ label, children }: SectionGroupProps): ReactElement {
  const headerStyle: CSSProperties = {
    padding: "10px 14px 4px",
    fontSize: "var(--font-size-2xs)",
    fontWeight: 600,
    letterSpacing: 0.4,
    textTransform: "uppercase",
    color: TEXT_FAINT,
  };
  return (
    <section data-testid={`agents-panel-section-${label.toLowerCase()}`}>
      <div style={headerStyle}>{label}</div>
      {children}
    </section>
  );
}

interface FilterRowProps {
  readonly label: string;
  readonly active: boolean;
  readonly onClick: () => void;
  readonly testId: string;
}

function FilterRow({
  label,
  active,
  onClick,
  testId,
}: FilterRowProps): ReactElement {
  const style: CSSProperties = {
    display: "block",
    width: "100%",
    textAlign: "left",
    padding: "6px 14px",
    fontSize: "var(--font-size-xs)",
    background: active
      ? "color-mix(in srgb, var(--color-accent) 12%, transparent)"
      : "transparent",
    color: active ? ACCENT : TEXT_PRIMARY,
    border: "none",
    cursor: "pointer",
    fontFamily: "inherit",
    fontWeight: active ? 600 : 500,
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
    borderRadius: 999,
    fontSize: "var(--font-size-2xs)",
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

function EmptyRow({ hint }: { hint: string }): ReactElement {
  const style: CSSProperties = {
    padding: "6px 14px",
    fontSize: "var(--font-size-xs)",
    color: TEXT_SECONDARY,
    fontStyle: "italic",
  };
  return <div style={style}>{hint}</div>;
}

export { PANEL_WIDTH as AGENTS_PANEL_WIDTH };
