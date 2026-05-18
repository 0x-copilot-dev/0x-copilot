// LibraryPanel tests (P7-B1).
//
// Covers: source filter chips (All / Uploaded / Saved from chats / Synced),
// project filter chip (when projects are supplied), sort selector,
// upload primary action, footer slot.

import type { ProjectId } from "@enterprise-search/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LibraryPanel, type LibraryPanelProps } from "./LibraryPanel";
import type { ProjectFilterChipOption } from "../projects/ProjectFilterChip";

const asProjectId = (s: string): ProjectId => s as unknown as ProjectId;

function renderPanel(props: LibraryPanelProps = {}): void {
  render(<LibraryPanel {...props} />);
}

const PROJECT_OPTIONS: ReadonlyArray<ProjectFilterChipOption> = [
  {
    id: asProjectId("proj_acme"),
    name: "Acme renewal",
    icon_emoji: "🚀",
    color_hue: 30,
    status: "active",
    viewer_starred: false,
  },
];

describe("LibraryPanel", () => {
  it("renders the source filter chips with 'all' selected by default", () => {
    renderPanel();
    const section = screen.getByTestId("library-panel-section-source");
    expect(section).toBeInTheDocument();
    for (const slug of ["all", "user_upload", "agent_save", "connector_sync"]) {
      expect(
        section.querySelector(`[data-testid="filter-tab-${slug}"]`),
      ).not.toBeNull();
    }
    const allTab = section.querySelector(`[data-testid="filter-tab-all"]`);
    expect(allTab?.getAttribute("data-active")).toBe("true");
  });

  it("calls onSourceFilterChange when a source chip is clicked", () => {
    const onSourceFilterChange = vi.fn();
    renderPanel({ onSourceFilterChange });
    const section = screen.getByTestId("library-panel-section-source");
    const agentTab = section.querySelector(
      `[data-testid="filter-tab-agent_save"]`,
    );
    expect(agentTab).not.toBeNull();
    fireEvent.click(agentTab!);
    expect(onSourceFilterChange).toHaveBeenCalledWith("agent_save");
  });

  it("renders counts on each source chip when sourceCounts is supplied", () => {
    renderPanel({
      sourceCounts: {
        all: 12,
        user_upload: 5,
        agent_save: 4,
        connector_sync: 3,
      },
    });
    expect(screen.getByTestId("filter-tab-count-all")).toHaveTextContent("12");
    expect(
      screen.getByTestId("filter-tab-count-user_upload"),
    ).toHaveTextContent("5");
    expect(screen.getByTestId("filter-tab-count-agent_save")).toHaveTextContent(
      "4",
    );
    expect(
      screen.getByTestId("filter-tab-count-connector_sync"),
    ).toHaveTextContent("3");
  });

  it("renders the project filter section when projects are supplied", () => {
    renderPanel({ projects: PROJECT_OPTIONS });
    expect(
      screen.getByTestId("library-panel-section-project"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("project-filter-chip")).toBeInTheDocument();
  });

  it("omits the project filter section when projects are not supplied", () => {
    renderPanel();
    expect(
      screen.queryByTestId("library-panel-section-project"),
    ).not.toBeInTheDocument();
  });

  it("renders the sort selector with the allowlisted options", () => {
    renderPanel();
    const sort = screen.getByTestId("library-panel-sort") as HTMLSelectElement;
    expect(sort).toBeInTheDocument();
    expect(sort.value).toBe("updated_at:desc");
    const options = Array.from(sort.options).map((o) => o.value);
    expect(options).toEqual([
      "updated_at:desc",
      "created_at:desc",
      "name:asc",
      "name:desc",
      "last_accessed_at:desc",
      "size_bytes:desc",
    ]);
  });

  it("fires onSortChange when the sort selector changes", () => {
    const onSortChange = vi.fn();
    renderPanel({ onSortChange });
    fireEvent.change(screen.getByTestId("library-panel-sort"), {
      target: { value: "name:asc" },
    });
    expect(onSortChange).toHaveBeenCalledWith("name:asc");
  });

  it("renders the '+ Upload file' primary action when onUploadFile is supplied", () => {
    const onUploadFile = vi.fn();
    renderPanel({ onUploadFile });
    const primary = screen.getByTestId("context-panel-primary-action");
    expect(primary).toHaveTextContent("+ Upload file");
    fireEvent.click(primary);
    expect(onUploadFile).toHaveBeenCalledTimes(1);
  });

  it("omits the primary action when onUploadFile is not supplied", () => {
    renderPanel();
    expect(
      screen.queryByTestId("context-panel-primary-action"),
    ).not.toBeInTheDocument();
  });

  it("renders the optional footer slot when supplied", () => {
    renderPanel({
      footer: <a data-testid="footer-link">Library guide</a>,
    });
    expect(
      screen.getByTestId("library-panel-section-footer"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("footer-link")).toHaveTextContent(
      "Library guide",
    );
  });
});
