import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type Mock,
} from "vitest";

import type {
  ChatArchiveRow,
  ConversationId,
  Project,
  ProjectId,
  ProjectListResponse,
  ProjectMembership,
  ProjectStreamEnvelope,
  ProjectSummary,
  TenantId,
  UserId,
} from "@0x-copilot/api-types";

// Mock the projectsApi module so the tests don't have to drive the real
// fetch / SSE plumbing — that surface is covered in `projectsApi.test.ts`.
const projectsApiMocks = vi.hoisted(() => ({
  fetchProjects: vi.fn(),
  fetchProject: vi.fn(),
  fetchProjectMembers: vi.fn(),
  activateProject: vi.fn(),
  archiveProject: vi.fn(),
  deleteProject: vi.fn(),
  starProject: vi.fn(),
  unstarProject: vi.fn(),
  streamProjectEvents: vi.fn(),
}));
vi.mock("../../api/projectsApi", async () => {
  const actual = await vi.importActual<typeof import("../../api/projectsApi")>(
    "../../api/projectsApi",
  );
  return {
    ...actual,
    fetchProjects: projectsApiMocks.fetchProjects,
    fetchProject: projectsApiMocks.fetchProject,
    fetchProjectMembers: projectsApiMocks.fetchProjectMembers,
    activateProject: projectsApiMocks.activateProject,
    archiveProject: projectsApiMocks.archiveProject,
    deleteProject: projectsApiMocks.deleteProject,
    starProject: projectsApiMocks.starProject,
    unstarProject: projectsApiMocks.unstarProject,
    streamProjectEvents: projectsApiMocks.streamProjectEvents,
  };
});

// PRD-07 — the detail pane's Chats + Files sections are fed by the web
// `ProjectDataPort` (over the HTTP client), NOT the old project-activity read
// (which hit a route that never existed). Mock the port module so tests drive
// those sections without the real fetch, mirroring the projectsApi seam.
const projectDataPortMocks = vi.hoisted(() => ({
  listProjectChats: vi.fn(),
  listProjectFiles: vi.fn(),
}));
vi.mock("./ProjectDataPort", () => ({
  createWebProjectDataPort: () => ({
    listProjectChats: projectDataPortMocks.listProjectChats,
    listProjectFiles: projectDataPortMocks.listProjectFiles,
  }),
}));

// Imports below this line resolve through the mocks above.
import { ProjectsRoute, applyProjectEnvelope } from "./ProjectsRoute";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

function summary(overrides: Partial<ProjectSummary> = {}): ProjectSummary {
  return {
    id: "project_1" as ProjectId,
    tenant_id: "tenant_1" as TenantId,
    name: "Q3 launch",
    description: "",
    icon_emoji: "🚀",
    color_hue: 220,
    status: "active",
    owner_user_id: "user_test" as UserId,
    viewer_role: "owner",
    viewer_starred: false,
    counts: {
      chats: 0,
      files: 0,
      todos_open: 0,
      todos_done: 0,
      inbox_items: 0,
      library_items: 0,
      routines_active: 0,
      members: 1,
    },
    last_activity_at: null,
    updated_at: "2026-05-18T09:00:00Z",
    ...overrides,
  };
}

function fullProject(overrides: Partial<Project> = {}): Project {
  return {
    id: "project_1" as ProjectId,
    tenant_id: "tenant_1" as TenantId,
    owner_user_id: "user_test" as UserId,
    name: "Q3 launch",
    description: "",
    icon_emoji: "🚀",
    color_hue: 220,
    status: "active",
    archived_at: null,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-18T09:00:00Z",
    last_activity_at: null,
    counts: {
      chats: 0,
      files: 0,
      todos_open: 0,
      todos_done: 0,
      inbox_items: 0,
      library_items: 0,
      routines_active: 0,
      members: 1,
    },
    viewer_role: "owner",
    viewer_starred: false,
    ...overrides,
  };
}

function listResponse(
  items: ReadonlyArray<ProjectSummary>,
): ProjectListResponse {
  return { items, next_cursor: null };
}

function membership(
  overrides: Partial<ProjectMembership> = {},
): ProjectMembership {
  return {
    project_id: "project_1" as ProjectId,
    user_id: "user_test" as UserId,
    role: "owner",
    added_at: "2026-05-01T00:00:00Z",
    added_by: "user_test" as UserId,
    ...overrides,
  };
}

function membersResponse(items: ReadonlyArray<ProjectMembership>) {
  return { items, next_cursor: null };
}

// PRD-07 — one project-scoped chat row, the shape `ProjectDataPort.
// listProjectChats` resolves (a `toChatArchiveRow` projection).
function chatArchiveRow(
  overrides: Partial<ChatArchiveRow> = {},
): ChatArchiveRow {
  return {
    id: "conv_1" as ConversationId,
    title: "Q3 kickoff",
    status: "done",
    preview: "Q3 kickoff notes",
    model: "gpt-4o",
    updated_at: "2026-05-18T09:00:00Z",
    pinned: false,
    ...overrides,
  };
}

function envelope(
  type: ProjectStreamEnvelope["event_type"],
  payload: ProjectStreamEnvelope["payload"],
  projectId: ProjectId,
  sequenceNo = 1,
): ProjectStreamEnvelope {
  return {
    sequence_no: sequenceNo,
    event_type: type,
    project_id: projectId,
    payload,
    emitted_at: "2026-05-18T09:00:00Z",
  };
}

function captureStreamCallbacks(closeMock = vi.fn()): {
  readonly close: Mock;
  readonly lastCall: () => {
    onEvent: (e: ProjectStreamEnvelope) => void;
    onError: (e: Event) => void;
    onOpen?: () => void;
  };
} {
  let lastCallbacks: {
    onEvent: (e: ProjectStreamEnvelope) => void;
    onError: (e: Event) => void;
    onOpen?: () => void;
  } = { onEvent: () => undefined, onError: () => undefined };
  projectsApiMocks.streamProjectEvents.mockImplementation(
    ({
      onEvent,
      onError,
      onOpen,
    }: {
      onEvent: (e: ProjectStreamEnvelope) => void;
      onError: (e: Event) => void;
      onOpen?: () => void;
    }) => {
      lastCallbacks = { onEvent, onError, onOpen };
      return { close: closeMock };
    },
  );
  return {
    close: closeMock,
    lastCall: () => lastCallbacks,
  };
}

// ===========================================================================
// PURE REDUCER — applyProjectEnvelope
// ===========================================================================

describe("applyProjectEnvelope", () => {
  it("prepends project_created when payload is a summary", () => {
    const a = summary({ id: "a" as ProjectId });
    const b = summary({ id: "b" as ProjectId });
    const next = applyProjectEnvelope(
      [a],
      envelope("project_created", b, "b" as ProjectId),
      "user_test",
    );
    expect(next.map((p) => p.id)).toEqual(["b", "a"]);
  });

  it("replaces in place on project_updated", () => {
    const a = summary({ id: "a" as ProjectId, name: "Old" });
    const aNew = summary({ id: "a" as ProjectId, name: "New" });
    const next = applyProjectEnvelope(
      [a],
      envelope("project_updated", aNew, "a" as ProjectId),
      "user_test",
    );
    expect(next[0].name).toBe("New");
  });

  it("replaces in place on project_archived / project_activated", () => {
    const a = summary({ id: "a" as ProjectId, status: "active" });
    const aArchived = summary({ id: "a" as ProjectId, status: "archived" });
    const after = applyProjectEnvelope(
      [a],
      envelope("project_archived", aArchived, "a" as ProjectId),
      "user_test",
    );
    expect(after[0].status).toBe("archived");

    const aActive = summary({ id: "a" as ProjectId, status: "active" });
    const reactivated = applyProjectEnvelope(
      after,
      envelope("project_activated", aActive, "a" as ProjectId),
      "user_test",
    );
    expect(reactivated[0].status).toBe("active");
  });

  it("drops a row on project_deleted", () => {
    const a = summary({ id: "a" as ProjectId });
    const b = summary({ id: "b" as ProjectId });
    const next = applyProjectEnvelope(
      [a, b],
      envelope(
        "project_deleted",
        { project_id: "b" as ProjectId },
        "b" as ProjectId,
      ),
      "user_test",
    );
    expect(next.map((p) => p.id)).toEqual(["a"]);
  });

  it("drops a row on project_member_removed when viewer is the target", () => {
    const a = summary({ id: "a" as ProjectId });
    const b = summary({ id: "b" as ProjectId });
    const next = applyProjectEnvelope(
      [a, b],
      envelope(
        "project_member_removed",
        { project_id: "b" as ProjectId, user_id: "user_test" as UserId },
        "b" as ProjectId,
      ),
      "user_test",
    );
    expect(next.map((p) => p.id)).toEqual(["a"]);
  });

  it("keeps the row on project_member_removed when another member was removed", () => {
    const a = summary({ id: "a" as ProjectId });
    const before = [a];
    const after = applyProjectEnvelope(
      before,
      envelope(
        "project_member_removed",
        { project_id: "a" as ProjectId, user_id: "user_other" as UserId },
        "a" as ProjectId,
      ),
      "user_test",
    );
    expect(after).toBe(before);
  });

  it("is a no-op for membership-add / role-change / ownership-transfer at the list layer", () => {
    const a = summary({ id: "a" as ProjectId });
    const before = [a];
    const memberAdded = applyProjectEnvelope(
      before,
      envelope(
        "project_member_added",
        { project_id: "a" as ProjectId, user_id: "user_other" as UserId },
        "a" as ProjectId,
      ),
      "user_test",
    );
    expect(memberAdded).toBe(before);

    const roleChanged = applyProjectEnvelope(
      before,
      envelope(
        "project_member_role_changed",
        { project_id: "a" as ProjectId, user_id: "user_other" as UserId },
        "a" as ProjectId,
      ),
      "user_test",
    );
    expect(roleChanged).toBe(before);
  });

  it("returns the same array on project_deleted for an unknown id", () => {
    const a = summary({ id: "a" as ProjectId });
    const before = [a];
    const after = applyProjectEnvelope(
      before,
      envelope(
        "project_deleted",
        { project_id: "b" as ProjectId },
        "b" as ProjectId,
      ),
      "user_test",
    );
    expect(after).toBe(before);
  });
});

// ===========================================================================
// RENDER — happy + error paths
// ===========================================================================

describe("ProjectsRoute render", () => {
  beforeEach(() => {
    projectsApiMocks.fetchProjects.mockReset();
    projectsApiMocks.fetchProject.mockReset();
    projectsApiMocks.activateProject.mockReset();
    projectsApiMocks.archiveProject.mockReset();
    projectsApiMocks.deleteProject.mockReset();
    projectsApiMocks.starProject.mockReset();
    projectsApiMocks.unstarProject.mockReset();
    projectsApiMocks.streamProjectEvents.mockReset();
    projectsApiMocks.streamProjectEvents.mockReturnValue({ close: vi.fn() });
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the loading state, then the ready list", async () => {
    projectsApiMocks.fetchProjects.mockResolvedValueOnce(
      listResponse([summary({ name: "Q3 launch" })]),
    );

    render(<ProjectsRoute identity={IDENTITY} />);

    expect(screen.getByTestId("projects-route")).toHaveAttribute(
      "data-state",
      "loading",
    );

    await waitFor(() => {
      expect(screen.getByTestId("projects-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });
    expect(screen.getByText("Q3 launch")).toBeInTheDocument();
    expect(screen.getByTestId("projects-route")).toHaveAttribute(
      "data-item-count",
      "1",
    );
  });

  it("renders each project as a .grid3 card — colour tile + first letter + 'N chats · M files' (FR-G.4)", async () => {
    projectsApiMocks.fetchProjects.mockResolvedValueOnce(
      listResponse([
        summary({
          name: "quartz sprint",
          description: "Ship the widget",
          color_hue: 180,
          counts: {
            chats: 3,
            files: 2,
            todos_open: 0,
            todos_done: 0,
            inbox_items: 0,
            library_items: 2,
            routines_active: 0,
            members: 1,
          },
        }),
      ]),
    );

    render(<ProjectsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("projects-route-list")).toHaveClass(
        "projects-grid3",
      );
    });

    const row = screen.getByTestId("projects-route-row");
    expect(row).toHaveClass("projects-card");
    // The colour tile shows the uppercased first letter of the name.
    const tile = row.querySelector(".proj-ic");
    expect(tile?.textContent).toBe("Q");
    expect(tile).toHaveAttribute("data-color-hue", "180");
    // The description + counts line render.
    expect(screen.getByText("Ship the widget")).toBeInTheDocument();
    expect(screen.getByText("3 chats · 2 files")).toBeInTheDocument();
  });

  it("renders the empty state when the server returns no items", async () => {
    projectsApiMocks.fetchProjects.mockResolvedValueOnce(listResponse([]));
    render(<ProjectsRoute identity={IDENTITY} />);
    await waitFor(() => {
      expect(screen.getByTestId("projects-route-empty")).toBeInTheDocument();
    });
  });

  it("renders the error state on fetch failure and retries on click", async () => {
    projectsApiMocks.fetchProjects.mockRejectedValueOnce(new Error("boom"));
    projectsApiMocks.fetchProjects.mockResolvedValueOnce(
      listResponse([summary()]),
    );

    render(<ProjectsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("projects-route-error")).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("projects-route-error-message").textContent,
    ).toContain("boom");

    fireEvent.click(screen.getByTestId("projects-route-retry"));

    await waitFor(() => {
      expect(screen.getByTestId("projects-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });
    expect(projectsApiMocks.fetchProjects).toHaveBeenCalledTimes(2);
  });
});

// ===========================================================================
// SSE — deltas merge into the local list + membership refetch
// ===========================================================================

describe("ProjectsRoute SSE", () => {
  beforeEach(() => {
    projectsApiMocks.fetchProjects.mockReset();
    projectsApiMocks.fetchProject.mockReset();
    projectsApiMocks.streamProjectEvents.mockReset();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("subscribes after the initial load and merges project_created deltas", async () => {
    projectsApiMocks.fetchProjects.mockResolvedValueOnce(
      listResponse([summary({ id: "a" as ProjectId, name: "Alpha" })]),
    );
    const sse = captureStreamCallbacks();

    render(<ProjectsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(projectsApiMocks.streamProjectEvents).toHaveBeenCalledTimes(1);
    });

    act(() => {
      sse
        .lastCall()
        .onEvent(
          envelope(
            "project_created",
            summary({ id: "b" as ProjectId, name: "Bravo" }),
            "b" as ProjectId,
            1,
          ),
        );
    });

    await waitFor(() => {
      expect(screen.getByText("Bravo")).toBeInTheDocument();
    });
    expect(screen.getAllByTestId("projects-route-row")).toHaveLength(2);
  });

  it("drops a row on project_deleted", async () => {
    const a = summary({ id: "a" as ProjectId, name: "Alpha" });
    const b = summary({ id: "b" as ProjectId, name: "Bravo" });
    projectsApiMocks.fetchProjects.mockResolvedValueOnce(listResponse([a, b]));
    const sse = captureStreamCallbacks();

    render(<ProjectsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByText("Alpha")).toBeInTheDocument();
    });

    act(() => {
      sse
        .lastCall()
        .onEvent(
          envelope(
            "project_deleted",
            { project_id: "b" as ProjectId },
            "b" as ProjectId,
            2,
          ),
        );
    });

    await waitFor(() => {
      expect(screen.queryByText("Bravo")).not.toBeInTheDocument();
    });
  });

  it("auto-adds a project to the rail on project_member_added for the viewer (sub-PRD §3.8)", async () => {
    projectsApiMocks.fetchProjects.mockResolvedValueOnce(listResponse([]));
    projectsApiMocks.fetchProject.mockResolvedValueOnce(
      fullProject({ id: "new_project" as ProjectId, name: "Just-added" }),
    );
    const sse = captureStreamCallbacks();

    render(<ProjectsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(projectsApiMocks.streamProjectEvents).toHaveBeenCalledTimes(1);
    });

    act(() => {
      sse.lastCall().onEvent(
        envelope(
          "project_member_added",
          {
            project_id: "new_project" as ProjectId,
            user_id: "user_test" as UserId,
          },
          "new_project" as ProjectId,
          5,
        ),
      );
    });

    await waitFor(() => {
      expect(projectsApiMocks.fetchProject).toHaveBeenCalledWith(
        IDENTITY,
        "new_project",
      );
    });
    await waitFor(() => {
      expect(screen.getByText("Just-added")).toBeInTheDocument();
    });
  });

  it("does NOT refetch on project_member_added for another user", async () => {
    projectsApiMocks.fetchProjects.mockResolvedValueOnce(listResponse([]));
    const sse = captureStreamCallbacks();

    render(<ProjectsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(projectsApiMocks.streamProjectEvents).toHaveBeenCalledTimes(1);
    });

    act(() => {
      sse.lastCall().onEvent(
        envelope(
          "project_member_added",
          {
            project_id: "other_project" as ProjectId,
            user_id: "user_other" as UserId,
          },
          "other_project" as ProjectId,
          5,
        ),
      );
    });

    // Give any stray promise microtask a chance to flush before asserting.
    await Promise.resolve();
    expect(projectsApiMocks.fetchProject).not.toHaveBeenCalled();
  });

  it("closes the active stream when the stream errors out (reconnect is then scheduled)", async () => {
    projectsApiMocks.fetchProjects.mockResolvedValueOnce(
      listResponse([summary()]),
    );
    const sse = captureStreamCallbacks();

    render(<ProjectsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(projectsApiMocks.streamProjectEvents).toHaveBeenCalledTimes(1);
    });

    // Trigger an error → component closes the active handle and queues
    // an exponential-backoff reconnect via setTimeout. We assert on the
    // close itself (the observable side-effect); the reconnect timing
    // is covered structurally by the reducer + the RECONNECT_BACKOFF_*
    // constants and would otherwise require global timer mocking which
    // conflicts with React Testing Library's own polling under jsdom.
    act(() => {
      sse.lastCall().onError(new Event("error"));
    });
    expect(sse.close).toHaveBeenCalled();
  });
});

// ===========================================================================
// MUTATIONS — archive / activate / star / delete
// ===========================================================================

describe("ProjectsRoute mutations", () => {
  beforeEach(() => {
    projectsApiMocks.fetchProjects.mockReset();
    projectsApiMocks.fetchProject.mockReset();
    projectsApiMocks.activateProject.mockReset();
    projectsApiMocks.archiveProject.mockReset();
    projectsApiMocks.deleteProject.mockReset();
    projectsApiMocks.starProject.mockReset();
    projectsApiMocks.unstarProject.mockReset();
    projectsApiMocks.streamProjectEvents.mockReset();
    projectsApiMocks.streamProjectEvents.mockReturnValue({ close: vi.fn() });
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("calls archiveProject and merges the updated row", async () => {
    const a = summary({ id: "a" as ProjectId, status: "active" });
    projectsApiMocks.fetchProjects.mockResolvedValueOnce(listResponse([a]));
    projectsApiMocks.archiveProject.mockResolvedValueOnce(
      fullProject({ id: "a" as ProjectId, status: "archived" }),
    );

    render(<ProjectsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("projects-route-archive")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("projects-route-archive"));

    await waitFor(() => {
      expect(projectsApiMocks.archiveProject).toHaveBeenCalledWith(
        IDENTITY,
        "a",
      );
    });
    await waitFor(() => {
      expect(screen.getByTestId("projects-route-row")).toHaveAttribute(
        "data-project-status",
        "archived",
      );
    });
  });

  it("calls activateProject and merges the updated row", async () => {
    const a = summary({ id: "a" as ProjectId, status: "archived" });
    projectsApiMocks.fetchProjects.mockResolvedValueOnce(listResponse([a]));
    projectsApiMocks.activateProject.mockResolvedValueOnce(
      fullProject({ id: "a" as ProjectId, status: "active" }),
    );

    render(<ProjectsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("projects-route-activate")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("projects-route-activate"));

    await waitFor(() => {
      expect(projectsApiMocks.activateProject).toHaveBeenCalledWith(
        IDENTITY,
        "a",
      );
    });
    await waitFor(() => {
      expect(screen.getByTestId("projects-route-row")).toHaveAttribute(
        "data-project-status",
        "active",
      );
    });
  });

  it("calls starProject when the row is currently unstarred", async () => {
    const a = summary({ id: "a" as ProjectId, viewer_starred: false });
    projectsApiMocks.fetchProjects.mockResolvedValueOnce(listResponse([a]));
    projectsApiMocks.starProject.mockResolvedValueOnce(
      fullProject({ id: "a" as ProjectId, viewer_starred: true }),
    );

    render(<ProjectsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("projects-route-star")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("projects-route-star"));

    await waitFor(() => {
      expect(projectsApiMocks.starProject).toHaveBeenCalledWith(IDENTITY, "a");
    });
  });

  it("calls unstarProject when the row is currently starred", async () => {
    const a = summary({ id: "a" as ProjectId, viewer_starred: true });
    projectsApiMocks.fetchProjects.mockResolvedValueOnce(listResponse([a]));
    projectsApiMocks.unstarProject.mockResolvedValueOnce(
      fullProject({ id: "a" as ProjectId, viewer_starred: false }),
    );

    render(<ProjectsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("projects-route-star")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("projects-route-star"));

    await waitFor(() => {
      expect(projectsApiMocks.unstarProject).toHaveBeenCalledWith(
        IDENTITY,
        "a",
      );
    });
  });

  it("calls deleteProject and removes the row from the local list", async () => {
    const a = summary({ id: "a" as ProjectId });
    projectsApiMocks.fetchProjects.mockResolvedValueOnce(listResponse([a]));
    projectsApiMocks.deleteProject.mockResolvedValueOnce(undefined);

    render(<ProjectsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("projects-route-delete")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("projects-route-delete"));

    await waitFor(() => {
      expect(projectsApiMocks.deleteProject).toHaveBeenCalledWith(
        IDENTITY,
        "a",
      );
    });
    await waitFor(() => {
      expect(screen.getByTestId("projects-route-empty")).toBeInTheDocument();
    });
  });

  it("surfaces a pending-error banner when the mutation fails (and keeps rendering the list)", async () => {
    const a = summary({ id: "a" as ProjectId, status: "active" });
    projectsApiMocks.fetchProjects.mockResolvedValueOnce(listResponse([a]));
    projectsApiMocks.archiveProject.mockRejectedValueOnce(
      new Error("archive_forbidden"),
    );

    render(<ProjectsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("projects-route-archive")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("projects-route-archive"));

    await waitFor(() => {
      expect(
        screen.getByTestId("projects-route-pending-error"),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("projects-route-pending-error").textContent,
    ).toContain("archive_forbidden");
    // The list itself is still rendered — the user can retry.
    expect(screen.getByTestId("projects-route-row")).toBeInTheDocument();
  });
});

// ===========================================================================
// DETAIL BINDER — mount ProjectDetailView via the renderDetail slot
// (FR-4.11/4.12/4.13)
// ===========================================================================

describe("ProjectsRoute detail pane", () => {
  beforeEach(() => {
    projectsApiMocks.fetchProjects.mockReset();
    projectsApiMocks.fetchProject.mockReset();
    projectsApiMocks.fetchProjectMembers.mockReset();
    projectDataPortMocks.listProjectChats.mockReset();
    projectDataPortMocks.listProjectFiles.mockReset();
    projectsApiMocks.streamProjectEvents.mockReset();
    projectsApiMocks.streamProjectEvents.mockReturnValue({ close: vi.fn() });
    // Sensible detail defaults; individual tests override as needed.
    projectsApiMocks.fetchProject.mockResolvedValue(fullProject());
    projectsApiMocks.fetchProjectMembers.mockResolvedValue(
      membersResponse([membership()]),
    );
    // PRD-07 — the port resolves a `SectionResult`, never throws. Default to
    // empty chats + files; individual tests override.
    projectDataPortMocks.listProjectChats.mockResolvedValue({
      status: "ok",
      data: [],
    });
    projectDataPortMocks.listProjectFiles.mockResolvedValue({
      status: "ok",
      data: [],
    });
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  /** Render the list and click a row's Open button to focus the detail. */
  async function renderAndOpen(
    props: { onOpenRun?: (id: ConversationId) => void } = {},
  ): Promise<void> {
    render(<ProjectsRoute identity={IDENTITY} {...props} />);
    await waitFor(() => {
      expect(screen.getByTestId("projects-route-open")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("projects-route-open"));
  }

  it("opens the detail pane via a row's Open button and mounts ProjectDetailView (FR-4.11)", async () => {
    projectsApiMocks.fetchProjects.mockResolvedValueOnce(
      listResponse([summary({ name: "Q3 launch" })]),
    );

    await renderAndOpen();

    // The detail renders inside the destination's own renderDetail slot.
    const slot = await screen.findByTestId("projects-detail-slot");
    await waitFor(() => {
      expect(
        within(slot).getByTestId("project-detail-view"),
      ).toBeInTheDocument();
    });
    expect(screen.getByTestId("project-detail-name").textContent).toBe(
      "Q3 launch",
    );
    expect(projectsApiMocks.fetchProject).toHaveBeenCalledWith(
      IDENTITY,
      "project_1",
    );
    // Focus is reflected on the route wrapper.
    expect(screen.getByTestId("projects-route")).toHaveAttribute(
      "data-focused-project-id",
      "project_1",
    );

    // Back returns to the list.
    fireEvent.click(screen.getByTestId("projects-detail-back"));
    await waitFor(() => {
      expect(screen.getByTestId("projects-route-list")).toBeInTheDocument();
    });
  });

  it("renders the Files section empty state when the project has no files (PRD-07 FR-4.11)", async () => {
    projectsApiMocks.fetchProjects.mockResolvedValueOnce(
      listResponse([summary()]),
    );
    // Port default (from beforeEach): empty files → the Files section is WIRED
    // now (PRD-07), so it renders "No files yet", not the old "coming soon".
    await renderAndOpen();
    await screen.findByTestId("project-detail-view");

    const filesTab = await screen.findByTestId("project-files-tab");
    await waitFor(() =>
      expect(filesTab).toHaveAttribute("data-state", "empty"),
    );
    expect(screen.getByText("No files yet")).toBeInTheDocument();
  });

  it("opens Run from a project chat row through the injected onOpenRun callback (PRD-07 FR-4.12)", async () => {
    projectsApiMocks.fetchProjects.mockResolvedValueOnce(
      listResponse([summary()]),
    );
    // The project's chats come from `ProjectDataPort.listProjectChats` — a
    // `toChatArchiveRow` projection carrying model + status (the fields the old
    // activity-fed list could not).
    projectDataPortMocks.listProjectChats.mockResolvedValue({
      status: "ok",
      data: [
        chatArchiveRow({
          id: "conv_42" as ConversationId,
          title: "Renewal thread",
          model: "gpt-4o",
        }),
      ],
    });
    const onOpenRun = vi.fn();

    await renderAndOpen({ onOpenRun });
    await screen.findByTestId("project-detail-view");

    // Solo profile renders the Chats section inline; the row is a
    // `chat-archive-row` (shared `_shared/Row`), activated → onOpenChat →
    // onOpenRun with the conversation id.
    const chatRow = await screen.findByTestId("chat-archive-row");
    expect(chatRow).toHaveTextContent("Renewal thread");
    expect(chatRow).toHaveTextContent("gpt-4o");
    fireEvent.click(chatRow);
    expect(onOpenRun).toHaveBeenCalledWith("conv_42");
  });

  it("renders a member/role chip on the row only when viewer_role is non-null (FR-4.13)", async () => {
    projectsApiMocks.fetchProjects.mockResolvedValueOnce(
      listResponse([summary({ viewer_role: "owner" })]),
    );
    render(<ProjectsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(
        screen.getByTestId("projects-route-role-chip"),
      ).toBeInTheDocument();
    });
    expect(screen.getByTestId("projects-route-role-chip")).toHaveAttribute(
      "data-role",
      "owner",
    );
  });

  it("omits the member/role chip under the solo profile (viewer_role null) (FR-4.13)", async () => {
    projectsApiMocks.fetchProjects.mockResolvedValueOnce(
      listResponse([summary({ viewer_role: null })]),
    );
    render(<ProjectsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("projects-route-row")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("projects-route-role-chip"),
    ).not.toBeInTheDocument();
  });
});
