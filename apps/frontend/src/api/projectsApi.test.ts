import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { configureAuthBearerProvider } from "./http";
import {
  activateProject,
  addProjectMember,
  archiveProject,
  createProject,
  deleteProject,
  fetchProject,
  fetchProjectMembers,
  fetchProjects,
  patchProject,
  patchProjectMember,
  removeProjectMember,
  starProject,
  transferProjectOwnership,
  unstarProject,
} from "./projectsApi";
import type {
  CreateProjectRequest,
  Project,
  ProjectId,
  ProjectListResponse,
  ProjectMembership,
  ProjectMembershipListResponse,
  ProjectSummary,
  TenantId,
  UserId,
} from "@0x-copilot/api-types";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

function summaryFixture(
  overrides: Partial<ProjectSummary> = {},
): ProjectSummary {
  return {
    id: "project_1" as ProjectId,
    tenant_id: "tenant_1" as TenantId,
    name: "Q3 launch",
    description: "Cross-functional launch coordination.",
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

function projectFixture(overrides: Partial<Project> = {}): Project {
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

function listFixture(
  items: ReadonlyArray<ProjectSummary>,
): ProjectListResponse {
  return { items, next_cursor: null };
}

function membershipFixture(
  overrides: Partial<ProjectMembership> = {},
): ProjectMembership {
  return {
    project_id: "project_1" as ProjectId,
    user_id: "user_member" as UserId,
    role: "editor",
    added_at: "2026-05-10T00:00:00Z",
    added_by: "user_test" as UserId,
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function emptyResponse(status = 204): Response {
  return new Response(null, { status });
}

function fetchMockReturning(
  responder: () => Response,
): ReturnType<typeof vi.fn> {
  return vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    responder(),
  );
}

// ===========================================================================
// LIST — happy + error paths + filter encoding
// ===========================================================================

describe("fetchProjects", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/projects with identity and no extras when called bare", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(listFixture([summaryFixture()])),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await fetchProjects(IDENTITY);

    expect(res.items).toHaveLength(1);
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/projects");
    expect(url).toContain("org_id=org_test");
    expect(url).toContain("user_id=user_test");
    expect(url).not.toContain("filter%5B");
    expect(url).not.toContain("sort=");
    // Facade-only invariant: caller never sees an absolute backend URL.
    expect(url).not.toMatch(/^https?:\/\/(127\.0\.0\.1|localhost):(8000|8100)/);
  });

  it("encodes filter axes + q + sort + cursor + limit", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(listFixture([])));
    vi.stubGlobal("fetch", fetchMock);

    await fetchProjects(IDENTITY, {
      filters: {
        status: "active",
        owner_user_id: "user_owner" as UserId,
        member_user_id: "user_member" as UserId,
        starred: true,
      },
      q: "launch",
      sort: "name:asc",
      after: "cursor_xyz",
      limit: 25,
    });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain(encodeURIComponent("filter[status]") + "=active");
    expect(url).toContain(
      encodeURIComponent("filter[owner_user_id]") + "=user_owner",
    );
    expect(url).toContain(
      encodeURIComponent("filter[member_user_id]") + "=user_member",
    );
    expect(url).toContain(encodeURIComponent("filter[starred]") + "=true");
    expect(url).toContain("q=launch");
    expect(url).toContain("sort=" + encodeURIComponent("name:asc"));
    expect(url).toContain("after=cursor_xyz");
    expect(url).toContain("limit=25");
  });

  it("omits filter[starred] when starred is false (caller-relative single value)", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(listFixture([])));
    vi.stubGlobal("fetch", fetchMock);

    await fetchProjects(IDENTITY, { filters: { starred: false } });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).not.toContain("starred");
  });

  it("omits the q param entirely when the search string is empty", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(listFixture([])));
    vi.stubGlobal("fetch", fetchMock);

    await fetchProjects(IDENTITY, { q: "" });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).not.toContain("q=");
  });

  it("surfaces server error messages from FastAPI's `detail` envelope", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "tenant_mismatch" }, 403),
      ),
    );

    await expect(fetchProjects(IDENTITY)).rejects.toThrow("tenant_mismatch");
  });
});

// ===========================================================================
// DETAIL
// ===========================================================================

describe("fetchProject", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/projects/{id} with URL-encoded id", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(projectFixture({ id: "prj/1 special" as ProjectId })),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchProject(IDENTITY, "prj/1 special" as ProjectId);

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/projects/prj%2F1%20special");
  });

  it("propagates 404 as an Error", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "project_not_found" }, 404),
      ),
    );

    await expect(
      fetchProject(IDENTITY, "missing" as ProjectId),
    ).rejects.toThrow("project_not_found");
  });
});

// ===========================================================================
// MUTATIONS — create, patch, delete, archive, activate, star/unstar, transfer
// ===========================================================================

describe("createProject", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("POSTs /v1/projects with the create body", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(projectFixture()));
    vi.stubGlobal("fetch", fetchMock);

    const body: CreateProjectRequest = {
      name: "Q3 launch",
      description: "",
      icon_emoji: "🚀",
      color_hue: 220,
    };
    const res = await createProject(IDENTITY, body);

    expect(res.id).toBe("project_1");
    const call = fetchMock.mock.calls[0];
    expect(String(call[0])).toContain("/v1/projects");
    expect((call[1] as RequestInit).method).toBe("POST");
    expect(JSON.parse((call[1] as RequestInit).body as string)).toMatchObject({
      name: "Q3 launch",
      icon_emoji: "🚀",
    });
  });
});

describe("patchProject", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("PATCHes /v1/projects/{id} with the partial body", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(projectFixture({ name: "Renamed" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await patchProject(IDENTITY, "project_1" as ProjectId, {
      name: "Renamed",
    });

    expect(res.name).toBe("Renamed");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("PATCH");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/projects/project_1",
    );
  });

  it("surfaces 403 owner-only-write errors", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() => jsonResponse({ detail: "forbidden" }, 403)),
    );
    await expect(
      patchProject(IDENTITY, "project_1" as ProjectId, { name: "x" }),
    ).rejects.toThrow("forbidden");
  });
});

describe("deleteProject", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("DELETEs /v1/projects/{id}", async () => {
    const fetchMock = fetchMockReturning(() => emptyResponse(204));
    vi.stubGlobal("fetch", fetchMock);

    await deleteProject(IDENTITY, "project_1" as ProjectId);

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/projects/project_1",
    );
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("DELETE");
  });
});

describe("archiveProject + activateProject", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("POSTs /v1/projects/{id}/archive with empty body", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(
        projectFixture({
          status: "archived",
          archived_at: "2026-05-18T09:00:00Z",
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await archiveProject(IDENTITY, "project_1" as ProjectId);

    expect(res.status).toBe("archived");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/projects/project_1/archive",
    );
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
  });

  it("POSTs /v1/projects/{id}/activate with empty body", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(projectFixture({ status: "active" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await activateProject(IDENTITY, "project_1" as ProjectId);

    expect(res.status).toBe("active");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/projects/project_1/activate",
    );
  });
});

describe("starProject + unstarProject", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("POSTs /v1/projects/{id}/star", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(projectFixture({ viewer_starred: true })),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await starProject(IDENTITY, "project_1" as ProjectId);

    expect(res.viewer_starred).toBe(true);
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/projects/project_1/star",
    );
  });

  it("POSTs /v1/projects/{id}/unstar", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(projectFixture({ viewer_starred: false })),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await unstarProject(IDENTITY, "project_1" as ProjectId);

    expect(res.viewer_starred).toBe(false);
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/projects/project_1/unstar",
    );
  });
});

describe("transferProjectOwnership", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("POSTs /v1/projects/{id}/transfer with the new_owner_user_id body", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(projectFixture({ owner_user_id: "user_new" as UserId })),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await transferProjectOwnership(
      IDENTITY,
      "project_1" as ProjectId,
      {
        new_owner_user_id: "user_new" as UserId,
        previous_owner_new_role: "editor",
      },
    );

    expect(res.owner_user_id).toBe("user_new");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/projects/project_1/transfer",
    );
    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toEqual({
      new_owner_user_id: "user_new",
      previous_owner_new_role: "editor",
    });
  });
});

// ===========================================================================
// MEMBERS
// ===========================================================================

describe("project member endpoints", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/projects/{id}/members", async () => {
    const response: ProjectMembershipListResponse = {
      items: [membershipFixture()],
      next_cursor: null,
    };
    const fetchMock = fetchMockReturning(() => jsonResponse(response));
    vi.stubGlobal("fetch", fetchMock);

    const res = await fetchProjectMembers(IDENTITY, "project_1" as ProjectId);

    expect(res.items).toHaveLength(1);
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/projects/project_1/members",
    );
  });

  it("POSTs /v1/projects/{id}/members with user_id + role", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(membershipFixture({ role: "viewer" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await addProjectMember(IDENTITY, "project_1" as ProjectId, {
      user_id: "user_member" as UserId,
      role: "viewer",
    });

    expect(res.role).toBe("viewer");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toEqual({ user_id: "user_member", role: "viewer" });
  });

  it("PATCHes /v1/projects/{id}/members/{user_id} with new role", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(membershipFixture({ role: "owner" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    await patchProjectMember(
      IDENTITY,
      "project_1" as ProjectId,
      "user_member" as UserId,
      // Member-patch promotes only to editor/viewer; owner goes through the
      // ownership-transfer endpoint, so the patch role type excludes "owner".
      { role: "editor" },
    );

    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("PATCH");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/projects/project_1/members/user_member",
    );
  });

  it("DELETEs /v1/projects/{id}/members/{user_id}", async () => {
    const fetchMock = fetchMockReturning(() => emptyResponse(204));
    vi.stubGlobal("fetch", fetchMock);

    await removeProjectMember(
      IDENTITY,
      "project_1" as ProjectId,
      "user_member" as UserId,
    );

    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("DELETE");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/projects/project_1/members/user_member",
    );
  });

  it("supports the /members/me self-remove shortcut", async () => {
    const fetchMock = fetchMockReturning(() => emptyResponse(204));
    vi.stubGlobal("fetch", fetchMock);

    await removeProjectMember(IDENTITY, "project_1" as ProjectId, "me");

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/projects/project_1/members/me",
    );
  });
});

// ===========================================================================
// ACTIVITY
// ===========================================================================
//
// PRD-07 deleted the project-activity fetch — `GET /v1/projects/{id}/activity`
// was never implemented on any service. The project-scoped chat list is now the
// conversation list filtered by project (`ProjectDataPort.listProjectChats`,
// covered by apps/frontend/src/features/projects + the desktop binder tests).
