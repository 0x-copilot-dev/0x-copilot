import type { ProjectId, TenantId, UserId } from "@0x-copilot/api-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { configureAuthBearerProvider } from "./http";
import {
  deleteProjectTemplate,
  fetchProjectTemplate,
  fetchProjectTemplates,
  forkProjectTemplate,
  patchProjectTemplate,
  saveProjectAsTemplate,
} from "./projectTemplatesApi";
import type {
  ForkProjectTemplateRequest,
  ProjectTemplate,
  ProjectTemplateId,
  ProjectTemplateListResponse,
  ProjectTemplateSnapshot,
} from "./projectTemplatesApi";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

function snapshotFixture(
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

function templateFixture(
  overrides: Partial<ProjectTemplate> = {},
): ProjectTemplate {
  return {
    id: "tpl_1" as ProjectTemplateId,
    tenant_id: "tenant_1" as TenantId,
    owner_user_id: "user_test" as UserId,
    name: "Customer onboarding",
    description: "Saved configuration for the onboarding playbook.",
    snapshot: snapshotFixture(),
    source_project_id: null,
    created_at: "2026-05-10T09:00:00Z",
    updated_at: "2026-05-10T09:00:00Z",
    ...overrides,
  };
}

function listFixture(
  items: ReadonlyArray<ProjectTemplate>,
): ProjectTemplateListResponse {
  return { items, next_cursor: null };
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
// LIST
// ===========================================================================

describe("fetchProjectTemplates", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/project-templates with identity and no extras when called bare", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(listFixture([templateFixture()])),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await fetchProjectTemplates(IDENTITY);

    expect(res.items).toHaveLength(1);
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/project-templates");
    expect(url).toContain("org_id=org_test");
    expect(url).toContain("user_id=user_test");
    expect(url).not.toContain("filter%5B");
    expect(url).not.toContain("sort=");
    // Facade-only invariant: caller never sees an absolute backend URL.
    expect(url).not.toMatch(/^https?:\/\/(127\.0\.0\.1|localhost):(8000|8100)/);
  });

  it("encodes owner filter + q + sort + cursor + limit", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(listFixture([])));
    vi.stubGlobal("fetch", fetchMock);

    await fetchProjectTemplates(IDENTITY, {
      filters: { owner_user_id: "user_owner" as UserId },
      q: "onboarding",
      sort: "created_at:desc",
      after: "cursor_xyz",
      limit: 25,
    });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain(
      encodeURIComponent("filter[owner_user_id]") + "=user_owner",
    );
    expect(url).toContain("q=onboarding");
    expect(url).toContain("sort=" + encodeURIComponent("created_at:desc"));
    expect(url).toContain("after=cursor_xyz");
    expect(url).toContain("limit=25");
  });

  it("omits the q param when the search string is empty", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(listFixture([])));
    vi.stubGlobal("fetch", fetchMock);

    await fetchProjectTemplates(IDENTITY, { q: "" });

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

    await expect(fetchProjectTemplates(IDENTITY)).rejects.toThrow(
      "tenant_mismatch",
    );
  });
});

// ===========================================================================
// DETAIL
// ===========================================================================

describe("fetchProjectTemplate", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/project-templates/{id} with URL-encoded id", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(
        templateFixture({ id: "tpl/1 special" as ProjectTemplateId }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchProjectTemplate(IDENTITY, "tpl/1 special" as ProjectTemplateId);

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/project-templates/tpl%2F1%20special");
  });

  it("propagates 404 as an Error", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "template_not_found" }, 404),
      ),
    );

    await expect(
      fetchProjectTemplate(IDENTITY, "missing" as ProjectTemplateId),
    ).rejects.toThrow("template_not_found");
  });
});

// ===========================================================================
// MUTATIONS — patch / delete / fork / save-as-template
// ===========================================================================

describe("patchProjectTemplate", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("PATCHes /v1/project-templates/{id} with the partial body", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(templateFixture({ name: "Renamed" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await patchProjectTemplate(
      IDENTITY,
      "tpl_1" as ProjectTemplateId,
      { name: "Renamed" },
    );

    expect(res.name).toBe("Renamed");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("PATCH");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/project-templates/tpl_1",
    );
    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toEqual({
      name: "Renamed",
    });
  });

  it("surfaces a 422 when the server rejects a snapshot mutation attempt", async () => {
    // Sub-PRD §7.5: snapshot is immutable — but the contract on the
    // typed wrapper restricts callers to name/description. This test
    // pins the wrapper still surfaces 422s passed through by the server.
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "snapshot_immutable" }, 422),
      ),
    );
    await expect(
      patchProjectTemplate(IDENTITY, "tpl_1" as ProjectTemplateId, {
        name: "x",
      }),
    ).rejects.toThrow("snapshot_immutable");
  });
});

describe("deleteProjectTemplate", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("DELETEs /v1/project-templates/{id}", async () => {
    const fetchMock = fetchMockReturning(() => emptyResponse(204));
    vi.stubGlobal("fetch", fetchMock);

    await deleteProjectTemplate(IDENTITY, "tpl_1" as ProjectTemplateId);

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/project-templates/tpl_1",
    );
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("DELETE");
  });
});

describe("forkProjectTemplate", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("POSTs /v1/project-templates/{id}/fork with the fork body", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({ id: "project_new" as ProjectId }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const body: ForkProjectTemplateRequest = {
      name: "Q3 launch — Acme",
      description: "Forked from the standard launch template.",
      color_hue: 200,
      icon_emoji: "🎯",
      member_overrides: ["user_a" as UserId, "user_b" as UserId],
      connector_overrides: ["google_drive", "slack"],
    };
    const res = await forkProjectTemplate(
      IDENTITY,
      "tpl_1" as ProjectTemplateId,
      body,
    );

    expect(res.id).toBe("project_new");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/project-templates/tpl_1/fork",
    );
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toMatchObject({
      name: "Q3 launch — Acme",
      color_hue: 200,
      member_overrides: ["user_a", "user_b"],
      connector_overrides: ["google_drive", "slack"],
    });
  });

  it("surfaces 5xx fork-rollback errors so the caller can show the banner", async () => {
    // Sub-PRD §7.4 — fork is atomic; on any failure the server returns 5xx
    // and nothing lands. Wrapper must propagate so the UI can re-enable
    // the Fork button without a half-state.
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "fork_rolled_back" }, 500),
      ),
    );

    await expect(
      forkProjectTemplate(IDENTITY, "tpl_1" as ProjectTemplateId, {
        name: "x",
      }),
    ).rejects.toThrow("fork_rolled_back");
  });
});

describe("saveProjectAsTemplate", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("POSTs /v1/projects/{id}/save-as-template with the body", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(templateFixture({ name: "From project" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await saveProjectAsTemplate(
      IDENTITY,
      "project_1" as ProjectId,
      {
        name: "From project",
        description: "Snapshot of project_1.",
      },
    );

    expect(res.name).toBe("From project");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/projects/project_1/save-as-template",
    );
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
  });

  it("defaults the body to {} when no overrides are passed", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(templateFixture()));
    vi.stubGlobal("fetch", fetchMock);

    await saveProjectAsTemplate(IDENTITY, "project_1" as ProjectId);

    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toEqual({});
  });

  it("surfaces 403 when the caller is not the source project's owner", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() => jsonResponse({ detail: "forbidden" }, 403)),
    );
    await expect(
      saveProjectAsTemplate(IDENTITY, "project_1" as ProjectId, {
        name: "x",
      }),
    ).rejects.toThrow("forbidden");
  });
});
