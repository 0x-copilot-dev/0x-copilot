import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createAgent,
  duplicateAgent,
  fetchAgent,
  fetchAgents,
  fetchAgentUsage,
  fetchAgentVersions,
  installAgent,
  patchAgent,
  snapshotAgentVersion,
  uninstallAgent,
} from "./agentsApi";
import { configureAuthBearerProvider } from "./http";
import type {
  Agent,
  AgentId,
  AgentListResponse,
  AgentUsageResponse,
  AgentVersion,
  AgentVersionId,
  AgentVersionListResponse,
  ConnectorId,
  CreateAgentRequest,
  SkillId,
  TenantId,
  UserId,
} from "./_agents-stub";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

function agentFixture(overrides: Partial<Agent> = {}): Agent {
  return {
    id: "agent_1" as AgentId,
    tenant_id: "tenant_1" as TenantId,
    name: "Inbox Triage",
    slug: "inbox-triage",
    description: "Triage incoming approvals.",
    icon_emoji: "📥",
    color_hue: 220,
    version: 1,
    status: "available",
    origin: "system",
    owner_user_id: null,
    instructions: "You are a triage assistant.",
    model_default: {
      model_id: "anthropic:claude-sonnet-4-7-1m",
      reasoning_depth: "balanced",
    },
    connectors_default: [],
    skills: [],
    permissions: {
      autonomy: "manual_approval",
      max_tool_calls_per_run: 10,
      max_output_tokens: 4000,
      read_only: false,
    },
    memory_ref: null,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-18T09:00:00Z",
    viewer_install_status: "available",
    viewer_usage_7d: null,
    ...overrides,
  };
}

function versionFixture(overrides: Partial<AgentVersion> = {}): AgentVersion {
  return {
    id: "agentver_1" as AgentVersionId,
    agent_id: "agent_1" as AgentId,
    version: 1,
    instructions_snapshot: "You are a triage assistant.",
    model_default_snapshot: {
      model_id: "anthropic:claude-sonnet-4-7-1m",
      reasoning_depth: "balanced",
    },
    skills_snapshot: [],
    connectors_default_snapshot: [],
    permissions_snapshot: {
      autonomy: "manual_approval",
      max_tool_calls_per_run: 10,
      max_output_tokens: 4000,
      read_only: false,
    },
    created_at: "2026-05-18T09:00:00Z",
    created_by: "user_test" as UserId,
    label: null,
    ...overrides,
  };
}

function listFixture(items: ReadonlyArray<Agent>): AgentListResponse {
  return { items, next_cursor: null };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
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

describe("fetchAgents", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/agents with identity and no extras when called bare", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(listFixture([agentFixture()])),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await fetchAgents(IDENTITY);

    expect(res.items).toHaveLength(1);
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/agents");
    expect(url).toContain("org_id=org_test");
    expect(url).toContain("user_id=user_test");
    expect(url).not.toContain("filter%5B");
    expect(url).not.toContain("sort=");
    // Facade-only invariant: caller never sees an absolute backend URL.
    expect(url).not.toMatch(/^https?:\/\/(127\.0\.0\.1|localhost):(8000|8100)/);
  });

  it("encodes all filter axes + q + sort + cursor + limit", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(listFixture([])));
    vi.stubGlobal("fetch", fetchMock);

    await fetchAgents(IDENTITY, {
      filters: {
        origin: "system",
        status: "installed",
        skill_id: "skill_summarize" as SkillId,
        connector_id: "connector_slack" as ConnectorId,
        owner_user_id: "user_owner" as UserId,
      },
      q: "triage",
      sort: "usage.cost_usd_micro:desc",
      after: "cursor_xyz",
      limit: 25,
    });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain(encodeURIComponent("filter[origin]") + "=system");
    expect(url).toContain(encodeURIComponent("filter[status]") + "=installed");
    expect(url).toContain(
      encodeURIComponent("filter[skill_id]") + "=skill_summarize",
    );
    expect(url).toContain(
      encodeURIComponent("filter[connector_id]") + "=connector_slack",
    );
    expect(url).toContain(
      encodeURIComponent("filter[owner_user_id]") + "=user_owner",
    );
    expect(url).toContain("q=triage");
    expect(url).toContain(
      "sort=" + encodeURIComponent("usage.cost_usd_micro:desc"),
    );
    expect(url).toContain("after=cursor_xyz");
    expect(url).toContain("limit=25");
  });

  it("omits the q param entirely when the search string is empty", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(listFixture([])));
    vi.stubGlobal("fetch", fetchMock);

    await fetchAgents(IDENTITY, { q: "" });

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

    await expect(fetchAgents(IDENTITY)).rejects.toThrow("tenant_mismatch");
  });
});

// ===========================================================================
// DETAIL
// ===========================================================================

describe("fetchAgent", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/agents/{id} with URL-encoded id", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(agentFixture({ id: "agent/1 special" as AgentId })),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchAgent(IDENTITY, "agent/1 special" as AgentId);

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/agents/agent%2F1%20special");
  });

  it("propagates 404 as an Error (cross-audit §1.3: not found / not visible)", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "agent_not_found" }, 404),
      ),
    );

    await expect(fetchAgent(IDENTITY, "missing" as AgentId)).rejects.toThrow(
      "agent_not_found",
    );
  });
});

// ===========================================================================
// MUTATIONS — create + patch
// ===========================================================================

describe("createAgent", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("POSTs /v1/agents with the create body", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(agentFixture()));
    vi.stubGlobal("fetch", fetchMock);

    const body: CreateAgentRequest = {
      name: "Inbox Triage",
      description: "",
      icon_emoji: "📥",
      color_hue: 220,
      instructions: "You are a triage assistant.",
      model_default: {
        model_id: "anthropic:claude-sonnet-4-7-1m",
        reasoning_depth: "balanced",
      },
      connectors_default: [],
      skills: [],
      permissions: {
        autonomy: "manual_approval",
        max_tool_calls_per_run: 10,
        max_output_tokens: 4000,
        read_only: false,
      },
    };
    const res = await createAgent(IDENTITY, body);

    expect(res.id).toBe("agent_1");
    const call = fetchMock.mock.calls[0];
    expect(String(call[0])).toContain("/v1/agents");
    expect((call[1] as RequestInit).method).toBe("POST");
    expect(JSON.parse((call[1] as RequestInit).body as string)).toMatchObject({
      name: "Inbox Triage",
      icon_emoji: "📥",
    });
  });
});

describe("patchAgent", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("PATCHes /v1/agents/{id} with the partial body", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(agentFixture({ name: "Renamed" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await patchAgent(IDENTITY, "agent_1" as AgentId, {
      name: "Renamed",
    });

    expect(res.name).toBe("Renamed");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("PATCH");
    expect(String(fetchMock.mock.calls[0][0])).toContain("/v1/agents/agent_1");
  });

  it("surfaces 409 agent_origin_immutable for system/community origin", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "agent_origin_immutable" }, 409),
      ),
    );
    await expect(
      patchAgent(IDENTITY, "agent_1" as AgentId, { name: "x" }),
    ).rejects.toThrow("agent_origin_immutable");
  });
});

// ===========================================================================
// INSTALL / UNINSTALL
// ===========================================================================

describe("installAgent + uninstallAgent", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("POSTs /v1/agents/{id}/install with empty body by default", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(
        agentFixture({
          status: "installed",
          viewer_install_status: "installed",
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await installAgent(IDENTITY, "agent_1" as AgentId);

    expect(res.viewer_install_status).toBe("installed");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/agents/agent_1/install",
    );
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
    // No scope override → body is just {}.
    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toEqual({});
  });

  it("forwards scope=tenant when supplied", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(agentFixture()));
    vi.stubGlobal("fetch", fetchMock);

    await installAgent(IDENTITY, "agent_1" as AgentId, { scope: "tenant" });

    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toEqual({ scope: "tenant" });
  });

  it("POSTs /v1/agents/{id}/uninstall", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(
        agentFixture({
          viewer_install_status: "available",
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await uninstallAgent(IDENTITY, "agent_1" as AgentId);

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/agents/agent_1/uninstall",
    );
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
  });
});

// ===========================================================================
// VERSIONS — snapshot + list
// ===========================================================================

describe("snapshotAgentVersion", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("POSTs /v1/agents/{id}/versions with an optional label", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(versionFixture({ label: "Pre-Q3-release config" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await snapshotAgentVersion(IDENTITY, "agent_1" as AgentId, {
      label: "Pre-Q3-release config",
    });

    expect(res.label).toBe("Pre-Q3-release config");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/agents/agent_1/versions",
    );
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toEqual({ label: "Pre-Q3-release config" });
  });

  it("POSTs an empty body when no label is supplied", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(versionFixture()));
    vi.stubGlobal("fetch", fetchMock);

    await snapshotAgentVersion(IDENTITY, "agent_1" as AgentId);

    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toEqual({});
  });
});

describe("fetchAgentVersions", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/agents/{id}/versions with paging params", async () => {
    const response: AgentVersionListResponse = {
      items: [versionFixture()],
      next_cursor: null,
    };
    const fetchMock = fetchMockReturning(() => jsonResponse(response));
    vi.stubGlobal("fetch", fetchMock);

    await fetchAgentVersions(IDENTITY, "agent_1" as AgentId, {
      after: "cursor_v3",
      limit: 20,
    });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/agents/agent_1/versions");
    expect(url).toContain("after=cursor_v3");
    expect(url).toContain("limit=20");
  });

  it("omits paging params when not supplied", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({ items: [], next_cursor: null }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchAgentVersions(IDENTITY, "agent_1" as AgentId);

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).not.toContain("after=");
    expect(url).not.toContain("limit=");
  });
});

// ===========================================================================
// DUPLICATE
// ===========================================================================

describe("duplicateAgent", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("POSTs /v1/agents/{id}/duplicate with an optional rename", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(
        agentFixture({
          id: "agent_2" as AgentId,
          origin: "custom",
          owner_user_id: "user_test" as UserId,
          name: "Inbox Triage (custom)",
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await duplicateAgent(IDENTITY, "agent_1" as AgentId, {
      name: "Inbox Triage (custom)",
    });

    expect(res.origin).toBe("custom");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/agents/agent_1/duplicate",
    );
    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toEqual({ name: "Inbox Triage (custom)" });
  });

  it("POSTs an empty body when no name is supplied", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(
        agentFixture({
          id: "agent_2" as AgentId,
          origin: "custom",
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await duplicateAgent(IDENTITY, "agent_1" as AgentId);

    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toEqual({});
  });
});

// ===========================================================================
// USAGE
// ===========================================================================

describe("fetchAgentUsage", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/agents/{id}/usage with period + since filters", async () => {
    const response: AgentUsageResponse = {
      agent_id: "agent_1" as AgentId,
      period: "week",
      rollups: [],
      totals: {
        agent_id: "agent_1" as AgentId,
        period: "week",
        run_count: 0,
        token_in: 0,
        token_out: 0,
        cost_usd_micro: 0,
      },
    };
    const fetchMock = fetchMockReturning(() => jsonResponse(response));
    vi.stubGlobal("fetch", fetchMock);

    await fetchAgentUsage(IDENTITY, "agent_1" as AgentId, {
      period: "month",
      since: "2026-04-01T00:00:00Z",
    });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/agents/agent_1/usage");
    expect(url).toContain("period=month");
    expect(url).toContain(
      "since=" + encodeURIComponent("2026-04-01T00:00:00Z"),
    );
  });

  it("omits period + since when not supplied (lets server default)", async () => {
    const response: AgentUsageResponse = {
      agent_id: "agent_1" as AgentId,
      period: "week",
      rollups: [],
      totals: {
        agent_id: "agent_1" as AgentId,
        period: "week",
        run_count: 0,
        token_in: 0,
        token_out: 0,
        cost_usd_micro: 0,
      },
    };
    const fetchMock = fetchMockReturning(() => jsonResponse(response));
    vi.stubGlobal("fetch", fetchMock);

    await fetchAgentUsage(IDENTITY, "agent_1" as AgentId);

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).not.toContain("period=");
    expect(url).not.toContain("since=");
  });

  it("propagates 403 when caller cannot read the agent", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() => jsonResponse({ detail: "forbidden" }, 403)),
    );

    await expect(
      fetchAgentUsage(IDENTITY, "agent_1" as AgentId),
    ).rejects.toThrow("forbidden");
  });
});
