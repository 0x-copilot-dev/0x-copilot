// PersonDetailView — tabs + ACL gates + ItemLink rendering.

import type {
  AgentId,
  PersonDetailResponse,
  ProjectId,
  TenantId,
  UserId,
} from "@0x-copilot/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../providers/RouterProvider";
import {
  __resetItemRouteRegistryForTests,
  registerItemRoute,
} from "../../refs/registry";
import type { ArtifactRoute, Router } from "../../routing/router";

import { PersonDetailView } from "./PersonDetailView";

afterEach(() => {
  __resetItemRouteRegistryForTests();
});

const NOW = Date.parse("2026-05-18T12:00:00.000Z");

const noopRouter: Router<ArtifactRoute> = {
  current: () => ({ kind: "workspace", workspaceId: "w0" }) as ArtifactRoute,
  navigate: () => undefined,
  subscribe: () => () => undefined,
};

function makeDetail(
  over: Partial<PersonDetailResponse> = {},
): PersonDetailResponse {
  return {
    person: {
      id: "u_1" as UserId,
      tenant_id: "tnt_1" as TenantId,
      display_name: "Sarah Acme",
      email: "sarah@acme.test",
      role: "member",
      presence: "active",
      last_seen_at: "2026-05-18T10:00:00.000Z",
      joined_at: "2025-01-01T00:00:00.000Z",
      agents_count: 1,
      projects_count: 1,
      is_self: false,
    },
    agents: [{ kind: "agent", id: "agent_1" as AgentId }],
    projects: [{ kind: "project", id: "proj_1" as ProjectId }],
    recent_activity: [],
    ...over,
  };
}

function renderInProvider(ui: React.ReactElement): void {
  render(<RouterProvider router={noopRouter}>{ui}</RouterProvider>);
}

describe("PersonDetailView", () => {
  it("renders a skeleton when detail is null", () => {
    renderInProvider(<PersonDetailView detail={null} isAdmin={false} />);
    expect(screen.getByTestId("person-detail-skeleton")).toBeInTheDocument();
  });

  it("renders a tablist with three tabs for non-admin viewers", () => {
    renderInProvider(
      <PersonDetailView detail={makeDetail()} isAdmin={false} now={NOW} />,
    );
    const tablist = screen.getByRole("tablist", { name: "Person detail" });
    expect(tablist).toBeInTheDocument();
    expect(
      screen.getByTestId("person-detail-tab-overview"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("person-detail-tab-agents")).toBeInTheDocument();
    expect(
      screen.getByTestId("person-detail-tab-projects"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("person-detail-tab-activity"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("person-detail-tab-settings"),
    ).not.toBeInTheDocument();
  });

  it("ACL admin gate: admin viewers see Activity + Settings tabs", () => {
    renderInProvider(
      <PersonDetailView detail={makeDetail()} isAdmin={true} now={NOW} />,
    );
    expect(
      screen.getByTestId("person-detail-tab-activity"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("person-detail-tab-settings"),
    ).toBeInTheDocument();
  });

  it("switches tabs via click", () => {
    renderInProvider(
      <PersonDetailView detail={makeDetail()} isAdmin={false} now={NOW} />,
    );
    fireEvent.click(screen.getByTestId("person-detail-tab-agents"));
    expect(
      screen.getByTestId("person-detail-tabpanel-agents"),
    ).toBeInTheDocument();
  });

  it("renders agents/projects via ItemLink anchors when routes are registered", () => {
    registerItemRoute(
      "agent",
      () => ({ kind: "workspace", workspaceId: "w-a" }) as ArtifactRoute,
    );
    registerItemRoute(
      "project",
      () => ({ kind: "workspace", workspaceId: "w-p" }) as ArtifactRoute,
    );
    renderInProvider(
      <PersonDetailView detail={makeDetail()} isAdmin={false} now={NOW} />,
    );
    fireEvent.click(screen.getByTestId("person-detail-tab-agents"));
    // Routes registered → the refs render as interactive anchors. The label is
    // the caller-supplied kind noun (id-only refs, PRD-04 Non-goals).
    expect(screen.getAllByTestId("item-link").length).toBeGreaterThan(0);
  });

  it("Activity tab — empty state when recent_activity is empty", () => {
    renderInProvider(
      <PersonDetailView detail={makeDetail()} isAdmin={true} now={NOW} />,
    );
    fireEvent.click(screen.getByTestId("person-detail-tab-activity"));
    expect(
      screen.getByTestId("person-detail-activity-empty"),
    ).toBeInTheDocument();
  });

  it("Activity tab — renders rows when entries exist (admin)", async () => {
    registerItemRoute(
      "project",
      () => ({ kind: "workspace", workspaceId: "w-p" }) as ArtifactRoute,
    );
    const detail = makeDetail({
      recent_activity: [
        {
          at: "2026-05-18T11:00:00.000Z",
          summary: "edited project description",
          target: { kind: "project", id: "proj_1" as ProjectId },
        },
      ],
    });
    renderInProvider(
      <PersonDetailView detail={detail} isAdmin={true} now={NOW} />,
    );
    fireEvent.click(screen.getByTestId("person-detail-tab-activity"));
    expect(
      screen.getByTestId("person-detail-activity-list"),
    ).toBeInTheDocument();
  });

  it("Settings tab — fires onChangeRole and onOpenOffboarding", () => {
    const onChangeRole = vi.fn();
    const onOpenOffboarding = vi.fn();
    renderInProvider(
      <PersonDetailView
        detail={makeDetail()}
        isAdmin={true}
        now={NOW}
        onChangeRole={onChangeRole}
        onOpenOffboarding={onOpenOffboarding}
      />,
    );
    fireEvent.click(screen.getByTestId("person-detail-tab-settings"));
    fireEvent.change(screen.getByTestId("person-detail-role-select"), {
      target: { value: "admin" },
    });
    expect(onChangeRole).toHaveBeenCalledWith("admin");
    fireEvent.click(screen.getByTestId("person-detail-offboard-trigger"));
    expect(onOpenOffboarding).toHaveBeenCalledTimes(1);
  });

  it("Settings tab — disables role select when viewing self", () => {
    renderInProvider(
      <PersonDetailView
        detail={makeDetail({
          person: { ...makeDetail().person, is_self: true },
        })}
        isAdmin={true}
        now={NOW}
        onChangeRole={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("person-detail-tab-settings"));
    const sel = screen.getByTestId(
      "person-detail-role-select",
    ) as HTMLSelectElement;
    expect(sel.disabled).toBe(true);
  });
});
