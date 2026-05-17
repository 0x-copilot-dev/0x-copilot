// TodosPanel shell tests (P3-B1).
//
// Covers: primary filter chips (All / Mine), per-project chips, saved
// filter selector stub, inline-add slot delegation.

import type { ProjectId } from "@enterprise-search/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  TodosPanel,
  type TodosFilterSlug,
  type TodosPanelProps,
  type TodosProjectChip,
  type TodosSavedFilter,
} from "./TodosPanel";

function renderPanel(props: TodosPanelProps = {}): void {
  render(<TodosPanel {...props} />);
}

describe("TodosPanel", () => {
  it("renders the primary filter chips (All / Mine) with Mine as the default", () => {
    renderPanel();
    const mineTab = screen.getByTestId("filter-tab-mine");
    const allTab = screen.getByTestId("filter-tab-all");
    expect(mineTab).toHaveAttribute("aria-selected", "true");
    expect(allTab).toHaveAttribute("aria-selected", "false");
  });

  it("calls onFilterChange when a primary chip is clicked", () => {
    const onFilterChange = vi.fn();
    renderPanel({ onFilterChange });
    fireEvent.click(screen.getByTestId("filter-tab-all"));
    expect(onFilterChange).toHaveBeenCalledWith("all");
  });

  it("renders per-project chips and routes change events through", () => {
    const onFilterChange = vi.fn();
    const projects: ReadonlyArray<TodosProjectChip> = [
      {
        project_id: "proj_acme" as ProjectId,
        name: "Acme",
        icon_emoji: "🪐",
        count: 5,
      },
      {
        project_id: "proj_globex" as ProjectId,
        name: "Globex",
        count: 0,
      },
    ];
    renderPanel({ projects, onFilterChange });

    expect(
      screen.getByTestId("todos-panel-section-project-filters"),
    ).toBeInTheDocument();

    // Slug shape: project:<id>
    const acmeTab = screen.getByTestId("filter-tab-project:proj_acme");
    expect(acmeTab).toHaveTextContent(/acme/i);
    expect(acmeTab).toHaveTextContent("5");

    fireEvent.click(acmeTab);
    expect(onFilterChange).toHaveBeenCalledWith("project:proj_acme");
  });

  it("does NOT render the project filters section when no projects are supplied", () => {
    renderPanel();
    expect(
      screen.queryByTestId("todos-panel-section-project-filters"),
    ).toBeNull();
  });

  it("highlights the active project chip when filter is a project slug", () => {
    const projects: ReadonlyArray<TodosProjectChip> = [
      {
        project_id: "proj_acme" as ProjectId,
        name: "Acme",
      },
    ];
    renderPanel({
      filter: "project:proj_acme" as TodosFilterSlug,
      projects,
    });
    const acmeTab = screen.getByTestId("filter-tab-project:proj_acme");
    expect(acmeTab).toHaveAttribute("aria-selected", "true");
  });

  it("shows an empty hint and a Save-current-filter button when no saved filters", () => {
    renderPanel();
    expect(screen.getByTestId("todos-panel-saved-filters")).toHaveTextContent(
      /no saved filters/i,
    );
    expect(
      screen.getByTestId("todos-panel-save-current-filter"),
    ).toBeInTheDocument();
  });

  it("renders saved filters as clickable items", () => {
    const onSelectSavedFilter = vi.fn();
    const savedFilters: ReadonlyArray<TodosSavedFilter> = [
      { id: "sf_1", label: "High priority, this week" },
    ];
    renderPanel({ savedFilters, onSelectSavedFilter });
    const item = screen.getByTestId("todos-panel-saved-filter-item");
    expect(item).toHaveAttribute("data-filter-id", "sf_1");
    fireEvent.click(item);
    expect(onSelectSavedFilter).toHaveBeenCalledWith("sf_1");
  });

  it("Save-current-filter is a no-op when no handler is provided (Wave 4+ stub)", () => {
    renderPanel();
    const button = screen.getByTestId("todos-panel-save-current-filter");
    expect(() => fireEvent.click(button)).not.toThrow();
  });

  it("calls onSaveCurrentFilter when a handler is provided", () => {
    const onSaveCurrentFilter = vi.fn();
    renderPanel({ onSaveCurrentFilter });
    fireEvent.click(screen.getByTestId("todos-panel-save-current-filter"));
    expect(onSaveCurrentFilter).toHaveBeenCalledTimes(1);
  });

  it("renders the inline-add slot when supplied; otherwise shows a coming-soon hint", () => {
    const { rerender } = render(<TodosPanel />);
    // Default: no slot → coming-soon empty-state inside the inline-add section.
    expect(
      screen.getByTestId("todos-panel-section-inline-add"),
    ).toHaveTextContent(/inline add coming soon/i);

    const renderInlineAdd = vi.fn().mockReturnValue("INLINE_ADD_SLOT");
    rerender(<TodosPanel renderInlineAdd={renderInlineAdd} />);
    expect(
      screen.getByTestId("todos-panel-inline-add-slot"),
    ).toBeInTheDocument();
    expect(renderInlineAdd).toHaveBeenCalled();
  });

  it("renders openCount in the panel subtitle when provided", () => {
    renderPanel({ openCount: 7 });
    expect(screen.getByTestId("context-panel-subtitle")).toHaveTextContent(
      /7 open/,
    );
  });
});
