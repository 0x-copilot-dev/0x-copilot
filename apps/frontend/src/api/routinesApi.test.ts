import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { configureAuthBearerProvider } from "./http";
import {
  activateRoutine,
  createRoutine,
  dismissRoutine,
  fetchRoutine,
  fetchRoutines,
  patchRoutine,
  pauseRoutine,
  runRoutineNow,
} from "./routinesApi";
import type {
  ConnectorId,
  CreateRoutineRequest,
  ListRoutinesResponse,
  ManualFireResponse,
  ProjectId,
  Routine,
  RoutineId,
  TenantId,
  TriggerId,
  UserId,
} from "./_routines-stub";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

function routineFixture(overrides: Partial<Routine> = {}): Routine {
  return {
    id: "routine_1" as RoutineId,
    tenant_id: "tenant_1" as TenantId,
    owner_user_id: "user_test" as UserId,
    project_id: null,
    name: "Daily brief",
    description: "Morning summary at 09:00 UTC.",
    instructions: "Summarise yesterday's mentions.",
    model: "gpt-5-mini",
    depth: "balanced",
    base_agent_id: null,
    status: "active",
    pause_reason: null,
    triggers: [
      {
        kind: "schedule",
        trigger_id: "trigger_1" as TriggerId,
        cron: "0 9 * * *",
        tz: "UTC",
      },
    ],
    connectors: [
      {
        connector_id: "connector_slack" as ConnectorId,
        mode: "inherit",
      },
    ],
    behavior: {
      autonomy: "manual_approval",
      max_retries: 3,
      backoff: "exponential",
      backoff_base_seconds: 30,
      max_duration_seconds: 600,
      output_target: { kind: "inbox" },
      notify_on_success: [],
      notify_on_failure: ["owner"],
    },
    permissions: {
      scope: "read_only",
      allowed_tools: [],
      allowed_skills: [],
      max_tool_calls_per_fire: 10,
      max_output_tokens_per_fire: 4_000,
      data_residency: "inherit",
      manual_fire: "owner",
    },
    missed_fire_policy: "fire_once",
    next_fire_at: "2026-05-19T09:00:00Z",
    last_fire_at: "2026-05-18T09:00:00Z",
    last_fire_status: "succeeded",
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-18T09:00:00Z",
    ...overrides,
  };
}

function listFixture(items: ReadonlyArray<Routine>): ListRoutinesResponse {
  return { items, next_cursor: null };
}

function fireFixture(): ManualFireResponse {
  return { run_ref: { kind: "run", id: "run_42" } };
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

describe("fetchRoutines", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/routines with identity and no extras when called bare", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(listFixture([routineFixture()])),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await fetchRoutines(IDENTITY);

    expect(res.items).toHaveLength(1);
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/routines");
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

    await fetchRoutines(IDENTITY, {
      filters: {
        status: "active",
        project_id: "project_q3" as ProjectId,
        trigger_kind: "schedule",
        owner_user_id: "user_owner" as UserId,
      },
      q: "brief",
      sort: "next_fire_at:asc",
      after: "cursor_xyz",
      limit: 25,
    });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain(encodeURIComponent("filter[status]") + "=active");
    expect(url).toContain(
      encodeURIComponent("filter[project_id]") + "=project_q3",
    );
    expect(url).toContain(
      encodeURIComponent("filter[trigger_kind]") + "=schedule",
    );
    expect(url).toContain(
      encodeURIComponent("filter[owner_user_id]") + "=user_owner",
    );
    expect(url).toContain("q=brief");
    expect(url).toContain("sort=" + encodeURIComponent("next_fire_at:asc"));
    expect(url).toContain("after=cursor_xyz");
    expect(url).toContain("limit=25");
  });

  it("omits the q param entirely when the search string is empty", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(listFixture([])));
    vi.stubGlobal("fetch", fetchMock);

    await fetchRoutines(IDENTITY, { q: "" });

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

    await expect(fetchRoutines(IDENTITY)).rejects.toThrow("tenant_mismatch");
  });
});

// ===========================================================================
// DETAIL
// ===========================================================================

describe("fetchRoutine", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/routines/{id} with URL-encoded id", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(routineFixture({ id: "rtn/1 special" as RoutineId })),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchRoutine(IDENTITY, "rtn/1 special" as RoutineId);

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/routines/rtn%2F1%20special");
  });

  it("propagates 404 as an Error", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "routine_not_found" }, 404),
      ),
    );

    await expect(
      fetchRoutine(IDENTITY, "missing" as RoutineId),
    ).rejects.toThrow("routine_not_found");
  });
});

// ===========================================================================
// MUTATIONS — create, patch, dismiss, activate, pause
// ===========================================================================

describe("createRoutine", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("POSTs /v1/routines with the create body and identity query", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(routineFixture()));
    vi.stubGlobal("fetch", fetchMock);

    const body: CreateRoutineRequest = {
      owner_user_id: "user_test" as UserId,
      project_id: null,
      name: "Daily brief",
      description: "",
      instructions: "Summarise yesterday's mentions.",
      model: "gpt-5-mini",
      depth: "balanced",
      base_agent_id: null,
      status: "draft",
      triggers: [
        {
          kind: "schedule",
          trigger_id: "trigger_new" as TriggerId,
          cron: "0 9 * * *",
          tz: "UTC",
        },
      ],
      connectors: [],
      behavior: {
        autonomy: "manual_approval",
        max_retries: 3,
        backoff: "exponential",
        backoff_base_seconds: 30,
        max_duration_seconds: 600,
        output_target: { kind: "inbox" },
        notify_on_success: [],
        notify_on_failure: ["owner"],
      },
      permissions: {
        scope: "read_only",
        allowed_tools: [],
        allowed_skills: [],
        max_tool_calls_per_fire: 10,
        max_output_tokens_per_fire: 4_000,
        data_residency: "inherit",
        manual_fire: "owner",
      },
      missed_fire_policy: "fire_once",
    };
    const res = await createRoutine(IDENTITY, body);

    expect(res.id).toBe("routine_1");
    const call = fetchMock.mock.calls[0];
    expect(String(call[0])).toContain("/v1/routines");
    expect((call[1] as RequestInit).method).toBe("POST");
    expect(JSON.parse((call[1] as RequestInit).body as string)).toMatchObject({
      name: "Daily brief",
      model: "gpt-5-mini",
    });
  });
});

describe("patchRoutine", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("PATCHes /v1/routines/{id} with the partial body", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(routineFixture({ name: "Renamed" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await patchRoutine(IDENTITY, "routine_1" as RoutineId, {
      name: "Renamed",
    });

    expect(res.name).toBe("Renamed");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("PATCH");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/routines/routine_1",
    );
  });

  it("surfaces 403 owner-only-write errors", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() => jsonResponse({ detail: "forbidden" }, 403)),
    );
    await expect(
      patchRoutine(IDENTITY, "routine_1" as RoutineId, { name: "x" }),
    ).rejects.toThrow("forbidden");
  });
});

describe("dismissRoutine", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("DELETEs /v1/routines/{id}", async () => {
    const fetchMock = fetchMockReturning(() => emptyResponse(204));
    vi.stubGlobal("fetch", fetchMock);

    await dismissRoutine(IDENTITY, "routine_1" as RoutineId);

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/routines/routine_1",
    );
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("DELETE");
  });
});

describe("activateRoutine + pauseRoutine", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("POSTs /v1/routines/{id}/activate with empty body", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(routineFixture({ status: "active" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await activateRoutine(IDENTITY, "routine_1" as RoutineId);

    expect(res.status).toBe("active");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/routines/routine_1/activate",
    );
  });

  it("POSTs /v1/routines/{id}/pause with the optional pause_reason", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(
        routineFixture({ status: "paused", pause_reason: "scope shrink" }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await pauseRoutine(IDENTITY, "routine_1" as RoutineId, {
      pause_reason: "scope shrink",
    });

    expect(res.status).toBe("paused");
    expect(res.pause_reason).toBe("scope shrink");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/routines/routine_1/pause",
    );
    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toEqual({ pause_reason: "scope shrink" });
  });

  it("POSTs /v1/routines/{id}/pause with an empty body when no reason is supplied", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(routineFixture({ status: "paused" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    await pauseRoutine(IDENTITY, "routine_1" as RoutineId);

    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toEqual({});
  });
});

// ===========================================================================
// MANUAL FIRE — "Run now"
// ===========================================================================

describe("runRoutineNow", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("POSTs /v1/routines/{id}/run and returns the new run_ref", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(fireFixture()));
    vi.stubGlobal("fetch", fetchMock);

    const res = await runRoutineNow(IDENTITY, "routine_1" as RoutineId);

    expect(res.run_ref.kind).toBe("run");
    expect(res.run_ref.id).toBe("run_42");
    const call = fetchMock.mock.calls[0];
    expect(String(call[0])).toContain("/v1/routines/routine_1/run");
    expect((call[1] as RequestInit).method).toBe("POST");
  });

  it("surfaces a 403 when the caller is outside the manual_fire ACL", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "manual_fire_forbidden" }, 403),
      ),
    );

    await expect(
      runRoutineNow(IDENTITY, "routine_1" as RoutineId),
    ).rejects.toThrow("manual_fire_forbidden");
  });
});
