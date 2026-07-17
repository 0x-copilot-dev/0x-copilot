import type { ProjectId, TenantId, UserId } from "@0x-copilot/api-types";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ProjectTemplate,
  ProjectTemplateId,
  ProjectTemplateListResponse,
  ProjectTemplateSnapshot,
} from "../../api/projectTemplatesApi";

// Mock the projectTemplatesApi module so tests don't drive real fetch
// plumbing — that surface is covered in `projectTemplatesApi.test.ts`.
const apiMocks = vi.hoisted(() => ({
  fetchProjectTemplates: vi.fn(),
  fetchProjectTemplate: vi.fn(),
  forkProjectTemplate: vi.fn(),
  deleteProjectTemplate: vi.fn(),
  patchProjectTemplate: vi.fn(),
  saveProjectAsTemplate: vi.fn(),
}));
vi.mock("../../api/projectTemplatesApi", async () => {
  const actual = await vi.importActual<
    typeof import("../../api/projectTemplatesApi")
  >("../../api/projectTemplatesApi");
  return {
    ...actual,
    fetchProjectTemplates: apiMocks.fetchProjectTemplates,
    fetchProjectTemplate: apiMocks.fetchProjectTemplate,
    forkProjectTemplate: apiMocks.forkProjectTemplate,
    deleteProjectTemplate: apiMocks.deleteProjectTemplate,
    patchProjectTemplate: apiMocks.patchProjectTemplate,
    saveProjectAsTemplate: apiMocks.saveProjectAsTemplate,
  };
});

// Resolves through the mock above.
import { TemplateGalleryRoute } from "./TemplateGalleryRoute";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

function snapshot(
  overrides: Partial<ProjectTemplateSnapshot> = {},
): ProjectTemplateSnapshot {
  return {
    default_member_user_ids: [],
    default_connector_allowlist: null,
    color_hue: 220,
    icon_emoji: "🚀",
    seeded_todos: [],
    seeded_routines: [],
    ...overrides,
  };
}

function template(overrides: Partial<ProjectTemplate> = {}): ProjectTemplate {
  return {
    id: "tpl_1" as ProjectTemplateId,
    tenant_id: "tenant_1" as TenantId,
    owner_user_id: "user_test" as UserId,
    name: "Customer onboarding",
    description: "Standard onboarding template.",
    snapshot: snapshot(),
    source_project_id: null,
    created_at: "2026-05-10T09:00:00Z",
    updated_at: "2026-05-10T09:00:00Z",
    ...overrides,
  };
}

function listResponse(
  items: ReadonlyArray<ProjectTemplate>,
): ProjectTemplateListResponse {
  return { items, next_cursor: null };
}

// ===========================================================================
// RENDER — happy + error paths
// ===========================================================================

describe("TemplateGalleryRoute render", () => {
  beforeEach(() => {
    apiMocks.fetchProjectTemplates.mockReset();
    apiMocks.forkProjectTemplate.mockReset();
    apiMocks.deleteProjectTemplate.mockReset();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the loading state, then the ready list", async () => {
    apiMocks.fetchProjectTemplates.mockResolvedValueOnce(
      listResponse([template({ name: "Onboarding" })]),
    );

    render(<TemplateGalleryRoute identity={IDENTITY} />);

    expect(screen.getByTestId("template-gallery-route")).toHaveAttribute(
      "data-state",
      "loading",
    );

    await waitFor(() => {
      expect(screen.getByTestId("template-gallery-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });
    expect(screen.getByText("Onboarding")).toBeInTheDocument();
    expect(screen.getByTestId("template-gallery-route")).toHaveAttribute(
      "data-item-count",
      "1",
    );
  });

  it("requests created_at:desc sort + limit=50 on the initial fetch", async () => {
    apiMocks.fetchProjectTemplates.mockResolvedValueOnce(listResponse([]));
    render(<TemplateGalleryRoute identity={IDENTITY} />);
    await waitFor(() => {
      expect(apiMocks.fetchProjectTemplates).toHaveBeenCalledWith(IDENTITY, {
        sort: "created_at:desc",
        limit: 50,
      });
    });
  });

  it("renders the empty state when the server returns no items", async () => {
    apiMocks.fetchProjectTemplates.mockResolvedValueOnce(listResponse([]));
    render(<TemplateGalleryRoute identity={IDENTITY} />);
    await waitFor(() => {
      expect(
        screen.getByTestId("template-gallery-route-empty"),
      ).toBeInTheDocument();
    });
  });

  it("renders the error state on fetch failure and retries on click", async () => {
    apiMocks.fetchProjectTemplates.mockRejectedValueOnce(new Error("boom"));
    apiMocks.fetchProjectTemplates.mockResolvedValueOnce(
      listResponse([template()]),
    );

    render(<TemplateGalleryRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(
        screen.getByTestId("template-gallery-route-error"),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("template-gallery-route-error-message").textContent,
    ).toContain("boom");

    fireEvent.click(screen.getByTestId("template-gallery-route-retry"));

    await waitFor(() => {
      expect(screen.getByTestId("template-gallery-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });
    expect(apiMocks.fetchProjectTemplates).toHaveBeenCalledTimes(2);
  });
});

// ===========================================================================
// FILTER — "all" vs "mine"
// ===========================================================================

describe("TemplateGalleryRoute filter", () => {
  beforeEach(() => {
    apiMocks.fetchProjectTemplates.mockReset();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("toggles between all and mine (client-side projection)", async () => {
    // Use distinct names from the filter-button labels ("All" / "Mine") so
    // text queries don't collide with the controls.
    apiMocks.fetchProjectTemplates.mockResolvedValueOnce(
      listResponse([
        template({
          id: "mine_1" as ProjectTemplateId,
          name: "Owner-Self",
          owner_user_id: "user_test" as UserId,
        }),
        template({
          id: "theirs_1" as ProjectTemplateId,
          name: "Owner-Other",
          owner_user_id: "user_other" as UserId,
        }),
      ]),
    );

    render(<TemplateGalleryRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByText("Owner-Self")).toBeInTheDocument();
    });
    // Default: all → both rows visible
    expect(screen.getByText("Owner-Other")).toBeInTheDocument();
    expect(screen.getAllByTestId("template-gallery-route-card")).toHaveLength(
      2,
    );

    fireEvent.click(screen.getByTestId("template-gallery-route-filter-mine"));

    await waitFor(() => {
      expect(screen.getByTestId("template-gallery-route")).toHaveAttribute(
        "data-filter",
        "mine",
      );
    });
    expect(screen.queryByText("Owner-Other")).not.toBeInTheDocument();
    expect(screen.getByText("Owner-Self")).toBeInTheDocument();
  });

  it("only shows the delete affordance to the template owner", async () => {
    apiMocks.fetchProjectTemplates.mockResolvedValueOnce(
      listResponse([
        template({
          id: "mine_1" as ProjectTemplateId,
          name: "Mine",
          owner_user_id: "user_test" as UserId,
        }),
        template({
          id: "theirs_1" as ProjectTemplateId,
          name: "Theirs",
          owner_user_id: "user_other" as UserId,
        }),
      ]),
    );

    render(<TemplateGalleryRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByText("Theirs")).toBeInTheDocument();
    });

    const deleteButtons = screen.getAllByTestId(
      "template-gallery-route-delete",
    );
    // Only one delete affordance (for the owner-matched row).
    expect(deleteButtons).toHaveLength(1);
    expect(deleteButtons[0]).toHaveAttribute("data-template-id", "mine_1");
  });
});

// ===========================================================================
// MUTATIONS — fork / delete
// ===========================================================================

describe("TemplateGalleryRoute mutations", () => {
  beforeEach(() => {
    apiMocks.fetchProjectTemplates.mockReset();
    apiMocks.forkProjectTemplate.mockReset();
    apiMocks.deleteProjectTemplate.mockReset();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("forks the template and calls onForked with the new project id", async () => {
    apiMocks.fetchProjectTemplates.mockResolvedValueOnce(
      listResponse([template()]),
    );
    apiMocks.forkProjectTemplate.mockResolvedValueOnce({
      id: "project_new" as ProjectId,
    });

    const onForked = vi.fn();
    render(<TemplateGalleryRoute identity={IDENTITY} onForked={onForked} />);

    await waitFor(() => {
      expect(
        screen.getByTestId("template-gallery-route-fork"),
      ).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("template-gallery-route-fork"));

    await waitFor(() => {
      expect(apiMocks.forkProjectTemplate).toHaveBeenCalledTimes(1);
    });
    const [identity, id, body] = apiMocks.forkProjectTemplate.mock.calls[0];
    expect(identity).toEqual(IDENTITY);
    expect(id).toBe("tpl_1");
    // Body carries the prefill defaults from the template (sub-PRD §7.6
    // fork dialog "pre-filled from snapshot").
    expect(body.name).toBe("Customer onboarding");
    expect(body.icon_emoji).toBe("🚀");
    expect(body.color_hue).toBe(220);

    await waitFor(() => {
      expect(onForked).toHaveBeenCalledWith("project_new");
    });
  });

  it("surfaces a pending-error banner when fork fails (sub-PRD §7.4 rollback)", async () => {
    apiMocks.fetchProjectTemplates.mockResolvedValueOnce(
      listResponse([template()]),
    );
    apiMocks.forkProjectTemplate.mockRejectedValueOnce(
      new Error("fork_rolled_back"),
    );

    render(<TemplateGalleryRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(
        screen.getByTestId("template-gallery-route-fork"),
      ).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("template-gallery-route-fork"));

    await waitFor(() => {
      expect(
        screen.getByTestId("template-gallery-route-pending-error"),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("template-gallery-route-pending-error").textContent,
    ).toContain("fork_rolled_back");
    // List still rendered — caller can retry.
    expect(
      screen.getByTestId("template-gallery-route-card"),
    ).toBeInTheDocument();
  });

  it("deletes a template and removes the row from the local list", async () => {
    apiMocks.fetchProjectTemplates.mockResolvedValueOnce(
      listResponse([template({ id: "tpl_1" as ProjectTemplateId })]),
    );
    apiMocks.deleteProjectTemplate.mockResolvedValueOnce(undefined);

    render(<TemplateGalleryRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(
        screen.getByTestId("template-gallery-route-delete"),
      ).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("template-gallery-route-delete"));

    await waitFor(() => {
      expect(apiMocks.deleteProjectTemplate).toHaveBeenCalledWith(
        IDENTITY,
        "tpl_1",
      );
    });
    await waitFor(() => {
      expect(
        screen.getByTestId("template-gallery-route-empty"),
      ).toBeInTheDocument();
    });
  });

  it("surfaces a pending-error banner when delete fails (and keeps rendering the list)", async () => {
    apiMocks.fetchProjectTemplates.mockResolvedValueOnce(
      listResponse([template()]),
    );
    apiMocks.deleteProjectTemplate.mockRejectedValueOnce(
      new Error("delete_forbidden"),
    );

    render(<TemplateGalleryRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(
        screen.getByTestId("template-gallery-route-delete"),
      ).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("template-gallery-route-delete"));

    await waitFor(() => {
      expect(
        screen.getByTestId("template-gallery-route-pending-error"),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("template-gallery-route-pending-error").textContent,
    ).toContain("delete_forbidden");
    expect(
      screen.getByTestId("template-gallery-route-card"),
    ).toBeInTheDocument();
  });
});
