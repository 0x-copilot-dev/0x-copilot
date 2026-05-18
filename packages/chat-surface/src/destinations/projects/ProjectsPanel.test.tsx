// ProjectsPanel shell tests (P6-B1).
//
// Covers: status chips (All / Active / Archived / Starred), "New project"
// CTA, optional footer.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ProjectsPanel, type ProjectsPanelProps } from "./ProjectsPanel";

function renderPanel(props: ProjectsPanelProps = {}): void {
  render(<ProjectsPanel {...props} />);
}

describe("ProjectsPanel", () => {
  it("renders the four status filter chips with 'all' selected by default", () => {
    renderPanel();
    const statusSection = screen.getByTestId("projects-panel-section-status");
    expect(statusSection).toBeInTheDocument();
    for (const slug of ["all", "active", "archived", "starred"]) {
      expect(
        statusSection.querySelector(`[data-testid="filter-tab-${slug}"]`),
      ).not.toBeNull();
    }
    const allTab = statusSection.querySelector(
      `[data-testid="filter-tab-all"]`,
    );
    expect(allTab?.getAttribute("data-active")).toBe("true");
  });

  it("calls onStatusFilterChange when a status chip is clicked", () => {
    const onStatusFilterChange = vi.fn();
    renderPanel({ onStatusFilterChange });
    const statusSection = screen.getByTestId("projects-panel-section-status");
    const archivedBtn = statusSection.querySelector(
      `[data-testid="filter-tab-archived"]`,
    );
    expect(archivedBtn).not.toBeNull();
    fireEvent.click(archivedBtn!);
    expect(onStatusFilterChange).toHaveBeenCalledWith("archived");
  });

  it("renders counts on each chip when statusCounts is supplied", () => {
    renderPanel({
      statusCounts: { all: 13, active: 9, archived: 4, starred: 2 },
    });
    expect(screen.getByTestId("filter-tab-count-all")).toHaveTextContent("13");
    expect(screen.getByTestId("filter-tab-count-active")).toHaveTextContent(
      "9",
    );
    expect(screen.getByTestId("filter-tab-count-archived")).toHaveTextContent(
      "4",
    );
    expect(screen.getByTestId("filter-tab-count-starred")).toHaveTextContent(
      "2",
    );
  });

  it("renders the 'New project' primary action when onCreateProject is supplied", () => {
    const onCreateProject = vi.fn();
    renderPanel({ onCreateProject });
    const primaryAction = screen.getByTestId("context-panel-primary-action");
    expect(primaryAction).toBeInTheDocument();
    expect(primaryAction).toHaveTextContent("New project");
    fireEvent.click(primaryAction);
    expect(onCreateProject).toHaveBeenCalledTimes(1);
  });

  it("omits the primary action when onCreateProject is not supplied", () => {
    renderPanel();
    expect(
      screen.queryByTestId("context-panel-primary-action"),
    ).not.toBeInTheDocument();
  });

  it("renders the optional footer slot when supplied", () => {
    renderPanel({
      footer: <a data-testid="footer-link">ACL guide</a>,
    });
    expect(
      screen.getByTestId("projects-panel-section-footer"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("footer-link")).toHaveTextContent("ACL guide");
  });

  it("reflects the selected status filter via data-active", () => {
    renderPanel({ statusFilter: "starred" });
    const statusSection = screen.getByTestId("projects-panel-section-status");
    const starredBtn = statusSection.querySelector(
      `[data-testid="filter-tab-starred"]`,
    );
    expect(starredBtn?.getAttribute("data-active")).toBe("true");
  });
});
