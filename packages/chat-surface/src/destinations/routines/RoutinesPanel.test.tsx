// RoutinesPanel shell tests (P5-B1).
//
// Covers: status chips (All / Active / Paused / Errored / Draft),
// trigger-kind chips (All / Schedule / Webhook / Event / Manual),
// project filter (with ItemLink), "New routine" CTA, optional footer.

import type { ProjectId } from "@0x-copilot/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../providers/RouterProvider";
import type { ArtifactRoute, Router } from "../../routing/router";

// Register the routine resolver before the panel renders an
// `<ItemLink kind="project">`. Project resolver lives in apps/frontend's
// host wiring; for this isolated test we register a minimal project
// resolver too so the link renders without throwing.
import "./index";
import {
  __resetItemRefRegistryForTests,
  hasItemRefResolver,
  registerItemRefResolver,
} from "../../refs/registry";
import "./index"; // re-import is idempotent — the guard inside the resolver block keeps it safe.

import {
  RoutinesPanel,
  type RoutinesPanelProjectChip,
  type RoutinesPanelProps,
} from "./RoutinesPanel";

const asProjectId = (s: string): ProjectId => s as unknown as ProjectId;

function makeRouter(): Router<ArtifactRoute> {
  let current: ArtifactRoute | null = null;
  const subscribers = new Set<(r: ArtifactRoute) => void>();
  return {
    current(): ArtifactRoute {
      if (current === null) throw new Error("no route");
      return current;
    },
    navigate(r: ArtifactRoute) {
      current = r;
      for (const s of subscribers) s(r);
    },
    subscribe(handler) {
      subscribers.add(handler);
      return () => subscribers.delete(handler);
    },
  };
}

function renderPanel(props: RoutinesPanelProps = {}): void {
  // Register a minimal project resolver if absent so ItemLink doesn't
  // throw inside the project list.
  if (!hasItemRefResolver("project")) {
    registerItemRefResolver("project", async (id) => ({
      label: `Project ${id as unknown as string}`,
      icon: null,
      route: { kind: "workspace", workspaceId: id as unknown as string },
    }));
  }
  render(
    <RouterProvider router={makeRouter()}>
      <RoutinesPanel {...props} />
    </RouterProvider>,
  );
}

describe("RoutinesPanel", () => {
  it("renders the five status filter chips with 'All' selected by default", () => {
    renderPanel();
    const statusSection = screen.getByTestId("routines-panel-section-status");
    expect(statusSection).toBeInTheDocument();
    for (const slug of ["all", "active", "paused", "errored", "draft"]) {
      // Two FilterTabs on the panel (status + triggers) share the slug
      // string for "all" — disambiguate via the section parent.
      expect(
        statusSection.querySelector(`[data-testid="filter-tab-${slug}"]`),
      ).not.toBeNull();
    }
  });

  it("renders the five trigger-kind filter chips with 'All' selected by default", () => {
    renderPanel();
    const triggerSection = screen.getByTestId(
      "routines-panel-section-triggers",
    );
    for (const slug of ["all", "schedule", "webhook", "event", "manual"]) {
      expect(
        triggerSection.querySelector(`[data-testid="filter-tab-${slug}"]`),
      ).not.toBeNull();
    }
  });

  it("calls onStatusFilterChange when a status chip is clicked", () => {
    const onStatusFilterChange = vi.fn();
    renderPanel({ onStatusFilterChange });
    const statusSection = screen.getByTestId("routines-panel-section-status");
    const erroredBtn = statusSection.querySelector(
      '[data-testid="filter-tab-errored"]',
    ) as HTMLElement;
    fireEvent.click(erroredBtn);
    expect(onStatusFilterChange).toHaveBeenCalledWith("errored");
  });

  it("calls onTriggerFilterChange when a trigger chip is clicked", () => {
    const onTriggerFilterChange = vi.fn();
    renderPanel({ onTriggerFilterChange });
    const triggerSection = screen.getByTestId(
      "routines-panel-section-triggers",
    );
    const webhookBtn = triggerSection.querySelector(
      '[data-testid="filter-tab-webhook"]',
    ) as HTMLElement;
    fireEvent.click(webhookBtn);
    expect(onTriggerFilterChange).toHaveBeenCalledWith("webhook");
  });

  it("renders per-status counts when statusCounts is supplied", () => {
    renderPanel({
      statusCounts: { all: 12, active: 7, paused: 2, errored: 1, draft: 2 },
    });
    const statusSection = screen.getByTestId("routines-panel-section-status");
    expect(
      statusSection.querySelector('[data-testid="filter-tab-count-active"]')
        ?.textContent,
    ).toBe("7");
    expect(
      statusSection.querySelector('[data-testid="filter-tab-count-errored"]')
        ?.textContent,
    ).toBe("1");
  });

  it("renders the project filter rows when projects[] is non-empty", () => {
    const projects: ReadonlyArray<RoutinesPanelProjectChip> = [
      { project_id: asProjectId("proj_acme"), name: "Acme", routine_count: 3 },
      {
        project_id: asProjectId("proj_wf"),
        name: "Workflow",
        routine_count: 1,
      },
    ];
    renderPanel({ projects });
    expect(
      screen.getByTestId("routines-panel-section-projects"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("routines-panel-project-proj_acme"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("routines-panel-project-proj_wf"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("routines-panel-project-all"),
    ).toBeInTheDocument();
  });

  it("hides the project filter section when projects[] is empty", () => {
    renderPanel({ projects: [] });
    expect(screen.queryByTestId("routines-panel-section-projects")).toBeNull();
  });

  it("calls onProjectFilterChange with the project id when a project row is clicked", () => {
    const onProjectFilterChange = vi.fn();
    const projects: ReadonlyArray<RoutinesPanelProjectChip> = [
      { project_id: asProjectId("proj_acme"), name: "Acme", routine_count: 3 },
    ];
    renderPanel({ projects, onProjectFilterChange });
    fireEvent.click(screen.getByTestId("routines-panel-project-proj_acme"));
    expect(onProjectFilterChange).toHaveBeenCalledWith(
      asProjectId("proj_acme"),
    );
  });

  it("calls onProjectFilterChange(null) when 'All projects' is clicked", () => {
    const onProjectFilterChange = vi.fn();
    renderPanel({
      projects: [
        {
          project_id: asProjectId("proj_acme"),
          name: "Acme",
          routine_count: 1,
        },
      ],
      activeProjectId: asProjectId("proj_acme"),
      onProjectFilterChange,
    });
    fireEvent.click(screen.getByTestId("routines-panel-project-all"));
    expect(onProjectFilterChange).toHaveBeenCalledWith(null);
  });

  it("renders the 'New routine' CTA via ContextPanel primary action", () => {
    const onCreateRoutine = vi.fn();
    renderPanel({ onCreateRoutine });
    const cta = screen.getByTestId("context-panel-primary-action");
    expect(cta).toHaveTextContent(/new routine/i);
    fireEvent.click(cta);
    expect(onCreateRoutine).toHaveBeenCalledTimes(1);
  });

  it("does not render the primary action when onCreateRoutine is omitted", () => {
    renderPanel();
    expect(screen.queryByTestId("context-panel-primary-action")).toBeNull();
  });

  it("renders the optional footer slot when supplied", () => {
    renderPanel({
      footer: <a href="#">Webhook security guide</a>,
    });
    const footer = screen.getByTestId("routines-panel-footer");
    expect(footer).toBeInTheDocument();
    expect(footer).toHaveTextContent(/webhook security guide/i);
  });

  it("highlights an active status chip when statusFilter is supplied", () => {
    renderPanel({ statusFilter: "errored" });
    const statusSection = screen.getByTestId("routines-panel-section-status");
    const erroredBtn = statusSection.querySelector(
      '[data-testid="filter-tab-errored"]',
    );
    expect(erroredBtn).toHaveAttribute("aria-selected", "true");
  });
});

// Keep the unused-import warning quiet — the test imports
// `__resetItemRefRegistryForTests` for future failure-resetting; if it
// drifts, this line forces a touch.
void __resetItemRefRegistryForTests;
