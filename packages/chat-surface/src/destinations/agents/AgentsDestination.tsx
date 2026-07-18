import { Button, TextInput } from "@0x-copilot/design-system";
import {
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import { AgentCard } from "./AgentCard";
import {
  AGENT_FILTER_LABELS,
  STARTER_RECOMMENDATIONS,
  filterAgents,
  searchAgents,
  type AgentFilter,
  type AgentStub,
} from "./_agents-stub";

// Design tokens — values are CSS variables so Settings → Appearance flows
// through. Same convention as ToolsDestination / HomeDestination.
const BACKGROUND = "var(--color-bg)";
const BORDER = "var(--color-border)";
const TEXT_PRIMARY = "var(--color-text)";
const TEXT_SECONDARY = "var(--color-text-muted)";
const TEXT_FAINT = "var(--color-text-subtle)";
const ACCENT = "var(--color-accent)";
const SURFACE = "var(--color-bg-elevated)";

export interface AgentsDestinationProps {
  /**
   * Agent catalog rendered in the gallery. Defaults to an empty list,
   * which exercises the "My agents → 3-4 recommended starters" path
   * required by the UI/UX preamble. The data-binder phase (P8-C) will
   * wire a real fetch through `Transport`.
   */
  readonly agents?: ReadonlyArray<AgentStub>;
  /**
   * Toggle-install callback. The destination keeps a local installed-set
   * overlay for optimistic feedback; this fires on every click whether
   * we render an install or uninstall button.
   */
  readonly onToggleInstall?: (agent: AgentStub) => void;
  /** "Create custom agent" CTA — opens the editor in P8-B2. */
  readonly onCreateCustom?: () => void;
  /** "View details" — opens detail panel in P8-B2. */
  readonly onViewDetails?: (agent: AgentStub) => void;
}

/**
 * Agents gallery destination (P8-B1).
 *
 * App-Store / VSCode-extension gallery layout:
 *   - PageHeader row: title + "Create custom agent" CTA
 *   - Search bar (auto-focus on mount)
 *   - FilterTabs row: My agents / Installed / Available / Custom / By skill
 *   - CardGrid of <AgentCard>s
 *   - Empty "My agents" path → 3-4 starter recommendations
 *
 * Pure presentation. No transport, no router. The data-binder phase
 * wires real fetching from apps/frontend.
 */
export function AgentsDestination({
  agents,
  onToggleInstall,
  onCreateCustom,
  onViewDetails,
}: AgentsDestinationProps = {}): ReactElement {
  const sourceAgents = agents ?? [];

  const [filter, setFilter] = useState<AgentFilter>("my");
  const [search, setSearch] = useState("");

  const filtered = useMemo<ReadonlyArray<AgentStub>>(() => {
    // by_skill is a panel-level facet in the full implementation — at the
    // destination level we treat it as "show everything" when no panel skill
    // filter has been chosen. P8-B1 ships the destination + panel
    // independently; the data-binder phase connects them.
    const byFilter = filterAgents(sourceAgents, filter, null);
    return searchAgents(byFilter, search);
  }, [sourceAgents, filter, search]);

  // Empty "My agents" → render starter recommendations instead. This is
  // the App-Store gallery onboarding pattern in the UI/UX preamble.
  const showStarters =
    filter === "my" &&
    search.trim() === "" &&
    sourceAgents.filter((a) => a.installed).length === 0;

  const handleInstall = (agent: AgentStub): void => {
    if (onToggleInstall !== undefined) onToggleInstall(agent);
  };
  const handleViewDetails = (agent: AgentStub): void => {
    if (onViewDetails !== undefined) onViewDetails(agent);
  };

  // ---- Styles ----------------------------------------------------------

  const containerStyle: CSSProperties = {
    width: "100%",
    height: "100%",
    minHeight: 0,
    display: "flex",
    flexDirection: "column",
    backgroundColor: BACKGROUND,
    color: TEXT_PRIMARY,
    boxSizing: "border-box",
  };
  const headerStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "16px 20px 12px",
    borderBottom: `1px solid ${BORDER}`,
    gap: 12,
  };
  const titleStyle: CSSProperties = {
    fontSize: "var(--font-size-xl)",
    fontWeight: 600,
    margin: 0,
  };
  const subtitleStyle: CSSProperties = {
    fontSize: "var(--font-size-sm)",
    color: TEXT_SECONDARY,
    margin: 0,
  };
  const searchBarStyle: CSSProperties = {
    display: "flex",
    gap: 12,
    padding: "12px 20px",
    alignItems: "center",
    borderBottom: `1px solid ${BORDER}`,
    backgroundColor: BACKGROUND,
  };
  const tabsBarStyle: CSSProperties = {
    display: "flex",
    gap: 4,
    padding: "10px 20px",
    borderBottom: `1px solid ${BORDER}`,
    backgroundColor: BACKGROUND,
    overflowX: "auto",
  };
  const bodyStyle: CSSProperties = {
    flex: 1,
    minHeight: 0,
    overflow: "auto",
    padding: 16,
  };
  const gridStyle: CSSProperties = {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
    gap: 12,
  };
  const emptyStyle: CSSProperties = {
    padding: 24,
    color: TEXT_SECONDARY,
    fontSize: "var(--font-size-sm)",
    textAlign: "center",
  };
  const starterHeaderStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 6,
    padding: "16px 0",
    color: TEXT_PRIMARY,
  };

  // ---- Render ---------------------------------------------------------

  return (
    <section
      data-component="agents-destination"
      data-testid="agents-destination"
      aria-label="Agents destination"
      style={containerStyle}
    >
      <div style={headerStyle} data-testid="agents-header">
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <h1 style={titleStyle}>Agents</h1>
          <p style={subtitleStyle}>
            Browse, install, and customize the agents that work on your behalf.
          </p>
        </div>
        <Button
          variant="primary"
          size="md"
          onClick={() => onCreateCustom?.()}
          data-testid="agents-create-cta"
        >
          + Create custom agent
        </Button>
      </div>

      <div style={searchBarStyle} data-testid="agents-search-bar">
        <TextInput
          // autoFocus is the right primitive here — React handles the
          // post-mount focus call after the input is in the DOM, so we
          // don't need a ref + requestAnimationFrame dance.
          autoFocus
          aria-label="Search agents"
          data-testid="agents-search"
          placeholder="Search agents by name or description"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <div role="tablist" style={tabsBarStyle} aria-label="Agent filter tabs">
        {(["my", "installed", "available", "custom", "by_skill"] as const).map(
          (key) => {
            const active = filter === key;
            const tabStyle: CSSProperties = {
              padding: "6px 12px",
              borderRadius: 6,
              fontSize: "var(--font-size-sm)",
              fontWeight: active ? 600 : 500,
              color: active ? ACCENT : TEXT_SECONDARY,
              background: active
                ? "color-mix(in srgb, var(--color-accent) 12%, transparent)"
                : "transparent",
              border: "none",
              cursor: "pointer",
              fontFamily: "inherit",
              whiteSpace: "nowrap",
            };
            return (
              <button
                key={key}
                role="tab"
                type="button"
                aria-selected={active}
                data-testid={`agents-tab-${key}`}
                data-active={active ? "true" : "false"}
                onClick={() => setFilter(key)}
                style={tabStyle}
              >
                {AGENT_FILTER_LABELS[key]}
              </button>
            );
          },
        )}
      </div>

      <div style={bodyStyle} data-testid="agents-body">
        {showStarters ? (
          <div data-testid="agents-starters">
            <div style={starterHeaderStyle}>
              <strong style={{ fontSize: "var(--font-size-md)" }}>
                Get started — one-tap install
              </strong>
              <span
                style={{ fontSize: "var(--font-size-sm)", color: TEXT_FAINT }}
              >
                These recommended agents cover the most common jobs. Install one
                to give your team its first member.
              </span>
            </div>
            <div style={gridStyle} role="list" aria-label="Recommended agents">
              {STARTER_RECOMMENDATIONS.map((agent) => (
                <AgentCard
                  key={agent.id}
                  agent={agent}
                  onToggleInstall={handleInstall}
                  onViewDetails={handleViewDetails}
                />
              ))}
            </div>
          </div>
        ) : filtered.length === 0 ? (
          <div data-testid="agents-empty" style={emptyStyle}>
            {search.trim() === ""
              ? "No agents in this view yet."
              : "No agents match your search."}
          </div>
        ) : (
          <div style={gridStyle} role="list" aria-label="Agents">
            {filtered.map((agent) => (
              <AgentCard
                key={agent.id}
                agent={agent}
                onToggleInstall={handleInstall}
                onViewDetails={handleViewDetails}
              />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

// Re-export the local Agent stub for consumers that need to construct
// agents for the destination (tests, the data-binder phase). Keeping it
// surface-local until the wire contract lands in api-types/agents.ts.
export type { AgentStub } from "./_agents-stub";
