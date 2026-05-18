// ProjectFilterChip widget tests (P6-B1).
//
// Covers: dropdown open/close, search filtering, section grouping (starred /
// active / archived), single-select, "All projects" reset, empty-results
// state.

import type { ProjectId } from "@enterprise-search/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  ProjectFilterChip,
  type ProjectFilterChipOption,
  type ProjectFilterChipProps,
} from "./ProjectFilterChip";

const asProjectId = (s: string): ProjectId => s as unknown as ProjectId;

function makeOption(
  over: Partial<ProjectFilterChipOption>,
): ProjectFilterChipOption {
  return {
    id: asProjectId("proj_default"),
    name: "Default",
    icon_emoji: "📁",
    color_hue: 180,
    status: "active",
    viewer_starred: false,
    ...over,
  };
}

const PROJECTS: ReadonlyArray<ProjectFilterChipOption> = [
  makeOption({
    id: asProjectId("proj_acme"),
    name: "Acme renewal",
    viewer_starred: true,
  }),
  makeOption({
    id: asProjectId("proj_onboard"),
    name: "Onboarding redesign",
    viewer_starred: false,
  }),
  makeOption({
    id: asProjectId("proj_q1"),
    name: "Q1 launch",
    status: "archived",
    viewer_starred: false,
  }),
];

function renderChip(props: Partial<ProjectFilterChipProps> = {}): {
  onChange: ReturnType<typeof vi.fn>;
} {
  const onChange = vi.fn();
  render(
    <ProjectFilterChip
      projects={PROJECTS}
      value={null}
      onChange={onChange}
      {...props}
    />,
  );
  return { onChange };
}

describe("ProjectFilterChip", () => {
  it("renders the trigger button closed by default with the default label", () => {
    renderChip();
    const trigger = screen.getByTestId("project-filter-chip-trigger");
    expect(trigger).toHaveTextContent("Project");
    expect(
      screen.queryByTestId("project-filter-chip-dropdown"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("project-filter-chip")).toHaveAttribute(
      "data-open",
      "false",
    );
  });

  it("opens the dropdown when the trigger is clicked", () => {
    renderChip();
    fireEvent.click(screen.getByTestId("project-filter-chip-trigger"));
    expect(
      screen.getByTestId("project-filter-chip-dropdown"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("project-filter-chip")).toHaveAttribute(
      "data-open",
      "true",
    );
  });

  it("renders starred / active / archived sections with one option per project", () => {
    renderChip();
    fireEvent.click(screen.getByTestId("project-filter-chip-trigger"));
    expect(screen.getByText("Starred")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("Archived")).toBeInTheDocument();
    expect(
      screen.getByTestId("project-filter-chip-option-proj_acme"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("project-filter-chip-option-proj_onboard"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("project-filter-chip-option-proj_q1"),
    ).toBeInTheDocument();
    // "All projects" reset is always present at the top.
    expect(screen.getByTestId("project-filter-chip-all")).toBeInTheDocument();
  });

  it("calls onChange with the selected ProjectId when a row is clicked", () => {
    const { onChange } = renderChip();
    fireEvent.click(screen.getByTestId("project-filter-chip-trigger"));
    fireEvent.click(screen.getByTestId("project-filter-chip-option-proj_acme"));
    expect(onChange).toHaveBeenCalledWith(asProjectId("proj_acme"));
    // Selecting an option closes the dropdown.
    expect(
      screen.queryByTestId("project-filter-chip-dropdown"),
    ).not.toBeInTheDocument();
  });

  it("calls onChange with null when 'All projects' is clicked", () => {
    const { onChange } = renderChip({ value: asProjectId("proj_acme") });
    fireEvent.click(screen.getByTestId("project-filter-chip-trigger"));
    fireEvent.click(screen.getByTestId("project-filter-chip-all"));
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it("filters options by the search query (case-insensitive substring)", async () => {
    renderChip();
    fireEvent.click(screen.getByTestId("project-filter-chip-trigger"));
    const user = userEvent.setup();
    const input = screen.getByTestId("project-filter-chip-search");
    await user.type(input, "onboard");
    expect(
      screen.queryByTestId("project-filter-chip-option-proj_acme"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByTestId("project-filter-chip-option-proj_onboard"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("project-filter-chip-option-proj_q1"),
    ).not.toBeInTheDocument();
  });

  it("renders the empty state when no project matches the query", async () => {
    renderChip();
    fireEvent.click(screen.getByTestId("project-filter-chip-trigger"));
    const user = userEvent.setup();
    await user.type(
      screen.getByTestId("project-filter-chip-search"),
      "zzz-nothing",
    );
    expect(screen.getByTestId("project-filter-chip-empty")).toHaveTextContent(
      "No matching projects",
    );
  });

  it("shows the selected project's name as the trigger label", () => {
    renderChip({ value: asProjectId("proj_onboard") });
    const trigger = screen.getByTestId("project-filter-chip-trigger");
    expect(trigger).toHaveTextContent("Onboarding redesign");
  });

  it("marks the active option with aria-selected", () => {
    renderChip({ value: asProjectId("proj_acme") });
    fireEvent.click(screen.getByTestId("project-filter-chip-trigger"));
    const opt = screen.getByTestId("project-filter-chip-option-proj_acme");
    expect(opt).toHaveAttribute("aria-selected", "true");
    expect(opt).toHaveAttribute("data-active", "true");
  });

  it("closes the dropdown on Escape", () => {
    renderChip();
    fireEvent.click(screen.getByTestId("project-filter-chip-trigger"));
    expect(
      screen.getByTestId("project-filter-chip-dropdown"),
    ).toBeInTheDocument();
    // Dispatch Escape on the wrapper — the chip handles keyDown locally
    // so it stays substrate-agnostic (no `document` listener).
    fireEvent.keyDown(screen.getByTestId("project-filter-chip"), {
      key: "Escape",
    });
    expect(
      screen.queryByTestId("project-filter-chip-dropdown"),
    ).not.toBeInTheDocument();
  });

  it("hides sections that have no matching options", () => {
    // Only one starred, no archived in the filtered set.
    renderChip({
      projects: [
        makeOption({
          id: asProjectId("proj_starred_only"),
          name: "Solo",
          viewer_starred: true,
        }),
      ],
    });
    fireEvent.click(screen.getByTestId("project-filter-chip-trigger"));
    expect(screen.getByText("Starred")).toBeInTheDocument();
    expect(screen.queryByText("Active")).not.toBeInTheDocument();
    expect(screen.queryByText("Archived")).not.toBeInTheDocument();
  });
});
