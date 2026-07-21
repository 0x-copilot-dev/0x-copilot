// ProjectsDestination shell tests (P6-B1).
//
// Covers: loading skeleton, error/unavailable empty states, ready state
// rendering (status pill + counts + ItemLink), filter tab interaction,
// row actions (archive / activate / star / unstar), render-detail slot.

import type {
  ProjectId,
  SectionResult,
  TenantId,
  UserId,
} from "@0x-copilot/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../providers/RouterProvider";
import type { ArtifactRoute, Router } from "../../routing/router";

// Importing the destination's index registers kind `"project"` so
// the `<ItemLink kind="project">` on each card resolves without
// throwing.
import "./index";

import type { ProjectSummary } from "@0x-copilot/api-types";

import {
  ProjectsDestination,
  type ProjectsDestinationProps,
} from "./ProjectsDestination";

// ===========================================================================
// Helpers
// ===========================================================================

const asProjectId = (s: string): ProjectId => s as unknown as ProjectId;
const asTenantId = (s: string): TenantId => s as unknown as TenantId;
const asUserId = (s: string): UserId => s as unknown as UserId;

function makeRouter(): Router<ArtifactRoute> & {
  navigate: ReturnType<typeof vi.fn>;
} {
  let current: ArtifactRoute | null = null;
  const subscribers = new Set<(r: ArtifactRoute) => void>();
  const navigate = vi.fn((r: ArtifactRoute) => {
    current = r;
    for (const s of subscribers) s(r);
  });
  return {
    current(): ArtifactRoute {
      if (current === null) throw new Error("no route");
      return current;
    },
    navigate,
    subscribe(handler) {
      subscribers.add(handler);
      return () => subscribers.delete(handler);
    },
  };
}

function renderDest(props: ProjectsDestinationProps = {}): void {
  const router = makeRouter();
  render(
    <RouterProvider router={router}>
      <ProjectsDestination {...props} />
    </RouterProvider>,
  );
}

// ===========================================================================
// Fixtures
// ===========================================================================

const NOW = Date.parse("2026-05-17T12:00:00.000Z");

function ok<T>(data: T): SectionResult<T> {
  return { status: "ok", data };
}

function makeProject(over: Partial<ProjectSummary>): ProjectSummary {
  return {
    id: asProjectId("proj_default"),
    tenant_id: asTenantId("tenant_1"),
    name: "Default project",
    description: "",
    icon_emoji: "📁",
    color_hue: 180,
    status: "active",
    owner_user_id: asUserId("usr_alice"),
    owner_display_name: "Alice",
    viewer_role: "owner",
    viewer_starred: false,
    counts: {
      chats: 0,
      todos_open: 0,
      todos_done: 0,
      inbox_items: 0,
      library_items: 0,
      routines_active: 0,
      members: 1,
    },
    last_activity_at: null,
    updated_at: "2026-05-15T10:00:00.000Z",
    ...over,
  };
}

const ACME = makeProject({
  id: asProjectId("proj_acme"),
  name: "Acme renewal",
  description: "Push the Q4 renewal across the line.",
  status: "active",
  viewer_starred: true,
  counts: {
    chats: 12,
    todos_open: 4,
    todos_done: 2,
    inbox_items: 1,
    library_items: 7,
    routines_active: 2,
    members: 5,
  },
  last_activity_at: "2026-05-17T09:30:00.000Z",
});
const ONBOARDING = makeProject({
  id: asProjectId("proj_onboard"),
  name: "Onboarding redesign",
  status: "active",
  viewer_starred: false,
});
const ARCHIVED = makeProject({
  id: asProjectId("proj_old"),
  name: "Q1 launch",
  status: "archived",
  viewer_starred: false,
});

// ===========================================================================
// Tests
// ===========================================================================

describe("ProjectsDestination", () => {
  it("renders the loading skeleton when items is null", () => {
    renderDest({ items: null });
    const root = screen.getByTestId("projects-destination");
    expect(root).toHaveAttribute("data-state", "loading");
    expect(screen.getAllByTestId("projects-skeleton-card")).toHaveLength(6);
  });

  it("renders the error empty-state with retry when items.status is error", () => {
    const onRetry = vi.fn();
    renderDest({
      items: { status: "error", error: "network down" },
      onRetry,
    });
    expect(screen.getByTestId("projects-destination")).toHaveAttribute(
      "data-state",
      "error",
    );
    expect(screen.getByText("Could not load projects")).toBeInTheDocument();
    expect(screen.getByText("network down")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("empty-state-action"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("renders the unavailable empty-state when items.status is unavailable", () => {
    renderDest({
      items: { status: "unavailable", error: "feature flag off" },
    });
    expect(screen.getByTestId("projects-destination")).toHaveAttribute(
      "data-state",
      "unavailable",
    );
    expect(screen.getByText("Projects unavailable")).toBeInTheDocument();
  });

  it("renders the empty state when ready with no projects", () => {
    const onCreateProject = vi.fn();
    renderDest({ items: ok([]), onCreateProject });
    expect(screen.getByText("No projects yet")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("empty-state-action"));
    expect(onCreateProject).toHaveBeenCalledTimes(1);
  });

  it("renders one CardGrid card per project in the ready state", () => {
    renderDest({ items: ok([ACME, ONBOARDING, ARCHIVED]), now: NOW });
    expect(screen.getByTestId("projects-destination")).toHaveAttribute(
      "data-state",
      "ready",
    );
    const cards = screen.getAllByTestId("project-card");
    expect(cards).toHaveLength(3);
    expect(cards[0]!.getAttribute("data-project-id")).toBe("proj_acme");
    expect(
      screen.getByText("Push the Q4 renewal across the line."),
    ).toBeInTheDocument();
    expect(screen.getByTestId("card-grid")).toBeInTheDocument();
  });

  it("renders the status filter chips and fires onFilterChange on click", () => {
    const onFilterChange = vi.fn();
    renderDest({
      items: ok([ACME, ONBOARDING, ARCHIVED]),
      filter: "all",
      onFilterChange,
      counts: { all: 3, active: 2, archived: 1, starred: 1 },
    });
    for (const slug of ["all", "active", "archived", "starred"]) {
      expect(screen.getByTestId(`filter-tab-${slug}`)).toBeInTheDocument();
    }
    fireEvent.click(screen.getByTestId("filter-tab-starred"));
    expect(onFilterChange).toHaveBeenCalledWith("starred");
  });

  it("shows the New project primary action when onCreateProject is supplied", () => {
    const onCreateProject = vi.fn();
    renderDest({ items: ok([ACME]), onCreateProject });
    fireEvent.click(screen.getByTestId("page-header-primary-action"));
    expect(onCreateProject).toHaveBeenCalledTimes(1);
  });

  it("calls onArchiveProject when the Archive action is clicked on an active card", () => {
    const onArchiveProject = vi.fn();
    renderDest({ items: ok([ACME]), onArchiveProject });
    fireEvent.click(screen.getByTestId("project-card-archive"));
    expect(onArchiveProject).toHaveBeenCalledWith(ACME.id);
  });

  it("calls onActivateProject when the Activate action is clicked on an archived card", () => {
    const onActivateProject = vi.fn();
    renderDest({ items: ok([ARCHIVED]), onActivateProject });
    fireEvent.click(screen.getByTestId("project-card-activate"));
    expect(onActivateProject).toHaveBeenCalledWith(ARCHIVED.id);
  });

  it("calls onStarProject when the star button is clicked on an unstarred card", () => {
    const onStarProject = vi.fn();
    renderDest({ items: ok([ONBOARDING]), onStarProject });
    fireEvent.click(screen.getByTestId("project-card-star"));
    expect(onStarProject).toHaveBeenCalledWith(ONBOARDING.id);
  });

  it("calls onUnstarProject when the unstar button is clicked on a starred card", () => {
    const onUnstarProject = vi.fn();
    renderDest({ items: ok([ACME]), onUnstarProject });
    fireEvent.click(screen.getByTestId("project-card-unstar"));
    expect(onUnstarProject).toHaveBeenCalledWith(ACME.id);
  });

  it("renders the detail slot when renderDetail + focusedProjectId are supplied", () => {
    const renderDetail = vi.fn(({ projectId }) => (
      <div data-testid="my-detail">{projectId}</div>
    ));
    renderDest({
      items: ok([ACME, ONBOARDING]),
      renderDetail,
      focusedProjectId: ACME.id,
    });
    expect(screen.getByTestId("projects-detail-slot")).toBeInTheDocument();
    expect(screen.getByTestId("my-detail")).toHaveTextContent("proj_acme");
    // List body is suppressed while the detail slot is mounted.
    expect(screen.queryByTestId("project-card")).not.toBeInTheDocument();
  });

  it("calls onCloseDetail when the slot invokes onClose", () => {
    const onCloseDetail = vi.fn();
    renderDest({
      items: ok([ACME]),
      renderDetail: ({ onClose }) => (
        <button type="button" data-testid="close-detail" onClick={onClose}>
          close
        </button>
      ),
      focusedProjectId: ACME.id,
      onCloseDetail,
    });
    fireEvent.click(screen.getByTestId("close-detail"));
    expect(onCloseDetail).toHaveBeenCalledTimes(1);
  });
});
