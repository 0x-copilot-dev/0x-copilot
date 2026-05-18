import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AgentsPanel, type AgentsPanelProps } from "./AgentsPanel";

function renderPanel(
  overrides: Partial<AgentsPanelProps> = {},
): AgentsPanelProps {
  const props: AgentsPanelProps = {
    filter: "my",
    onFilterChange: vi.fn(),
    originFilter: null,
    onOriginFilterChange: vi.fn(),
    skillFilter: null,
    onSkillFilterChange: vi.fn(),
    connectorFilter: null,
    onConnectorFilterChange: vi.fn(),
    skills: ["web-search", "summarize"],
    connectors: ["gmail", "slack"],
    ...overrides,
  };
  render(<AgentsPanel {...props} />);
  return props;
}

describe("AgentsPanel", () => {
  it("renders all 5 view filter rows and marks the active one", () => {
    renderPanel({ filter: "available" });
    expect(screen.getByTestId("agents-panel-filter-my")).toHaveAttribute(
      "data-active",
      "false",
    );
    expect(screen.getByTestId("agents-panel-filter-available")).toHaveAttribute(
      "data-active",
      "true",
    );
    expect(
      screen.getByTestId("agents-panel-filter-by_skill"),
    ).toBeInTheDocument();
  });

  it("fires onFilterChange when a view row is clicked", () => {
    const props = renderPanel();
    fireEvent.click(screen.getByTestId("agents-panel-filter-custom"));
    expect(props.onFilterChange).toHaveBeenCalledWith("custom");
  });

  it("renders origin section with all-origins + 3 origin rows", () => {
    renderPanel({ originFilter: "installed" });
    expect(screen.getByTestId("agents-panel-origin-all")).toHaveAttribute(
      "data-active",
      "false",
    );
    expect(screen.getByTestId("agents-panel-origin-installed")).toHaveAttribute(
      "data-active",
      "true",
    );
  });

  it("fires onOriginFilterChange with null when 'All origins' is clicked", () => {
    const props = renderPanel({ originFilter: "installed" });
    fireEvent.click(screen.getByTestId("agents-panel-origin-all"));
    expect(props.onOriginFilterChange).toHaveBeenCalledWith(null);
  });

  it("renders skill chips and toggles them on click", () => {
    const props = renderPanel({ skillFilter: null });
    fireEvent.click(screen.getByTestId("agents-panel-skill-web-search"));
    expect(props.onSkillFilterChange).toHaveBeenCalledWith("web-search");
  });

  it("clicking the active skill chip clears the filter (toggle off)", () => {
    const props = renderPanel({ skillFilter: "web-search" });
    fireEvent.click(screen.getByTestId("agents-panel-skill-web-search"));
    expect(props.onSkillFilterChange).toHaveBeenCalledWith(null);
  });

  it("renders connector chips and toggles them on click", () => {
    const props = renderPanel({ connectorFilter: null });
    fireEvent.click(screen.getByTestId("agents-panel-connector-gmail"));
    expect(props.onConnectorFilterChange).toHaveBeenCalledWith("gmail");
  });

  it("shows an empty hint when there are no skills", () => {
    renderPanel({ skills: [] });
    expect(screen.queryByTestId("agents-panel-skill-row")).toBeNull();
  });
});
