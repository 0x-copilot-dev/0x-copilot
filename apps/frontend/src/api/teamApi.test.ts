import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  InviteRequest,
  OffboardingRequest,
  Person,
  PersonDetailResponse,
  TeamListResponse,
  TeamStreamEnvelope,
  TenantId,
  UserId,
} from "@0x-copilot/api-types";

import { configureAuthBearerProvider } from "./http";
import {
  fetchPerson,
  fetchTeam,
  invitePerson,
  offboardPerson,
  patchPersonRole,
  streamTeamEvents,
} from "./teamApi";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

function personFixture(overrides: Partial<Person> = {}): Person {
  return {
    id: "user_alice" as UserId,
    tenant_id: "tenant_1" as TenantId,
    display_name: "Alice",
    email: "alice@example.com",
    avatar_url: undefined,
    role: "member",
    presence: "active",
    last_seen_at: "2026-05-18T08:00:00Z",
    joined_at: "2026-01-01T00:00:00Z",
    agents_count: 0,
    projects_count: 0,
    is_self: false,
    ...overrides,
  };
}

function listFixture(items: ReadonlyArray<Person>): TeamListResponse {
  return { people: items, next_cursor: null };
}

function detailFixture(p: Person): PersonDetailResponse {
  return {
    person: p,
    agents: [],
    projects: [],
    recent_activity: [],
  };
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

describe("fetchTeam", () => {
  beforeEach(() => configureAuthBearerProvider(() => "test-bearer"));
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/team with identity and no filter when called bare", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(listFixture([personFixture()])),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await fetchTeam(IDENTITY);

    expect(res.people).toHaveLength(1);
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/team");
    expect(url).toContain("org_id=org_test");
    expect(url).toContain("user_id=user_test");
    expect(url).not.toContain("filter%5B");
    expect(url).not.toMatch(/^https?:\/\/(127\.0\.0\.1|localhost):(8000|8100)/);
  });

  it("encodes filter axes + q + sort + cursor + limit", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(listFixture([])));
    vi.stubGlobal("fetch", fetchMock);

    await fetchTeam(IDENTITY, {
      role: "admin",
      presence: "active",
      q: "alice",
      sort: "display_name:asc",
      after: "cur_x",
      limit: 25,
    });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain(encodeURIComponent("filter[role]") + "=admin");
    expect(url).toContain(encodeURIComponent("filter[presence]") + "=active");
    expect(url).toContain("q=alice");
    expect(url).toContain("sort=" + encodeURIComponent("display_name:asc"));
    expect(url).toContain("after=cur_x");
    expect(url).toContain("limit=25");
  });

  it("propagates 503 as an Error", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "team unavailable" }, 503),
      ),
    );
    await expect(fetchTeam(IDENTITY)).rejects.toThrow("team unavailable");
  });
});

describe("fetchPerson", () => {
  beforeEach(() => configureAuthBearerProvider(() => "test-bearer"));
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/team/{id} with URL-encoded id", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(detailFixture(personFixture())),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchPerson(IDENTITY, "user/alice 1" as UserId);

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/team/user%2Falice%201");
  });

  it("propagates 404 as an Error", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "person_not_found" }, 404),
      ),
    );
    await expect(fetchPerson(IDENTITY, "missing" as UserId)).rejects.toThrow(
      "person_not_found",
    );
  });
});

describe("invitePerson", () => {
  beforeEach(() => configureAuthBearerProvider(() => "test-bearer"));
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("POSTs /v1/team/invite with the invite body", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(personFixture({ email: "new@example.com" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    const body: InviteRequest = {
      email: "new@example.com",
      role: "member",
      note: "welcome",
    };
    const res = await invitePerson(IDENTITY, body);

    expect(res.email).toBe("new@example.com");
    const call = fetchMock.mock.calls[0];
    expect(String(call[0])).toContain("/v1/team/invite");
    expect((call[1] as RequestInit).method).toBe("POST");
    expect(JSON.parse((call[1] as RequestInit).body as string)).toMatchObject({
      email: "new@example.com",
      role: "member",
    });
  });
});

describe("patchPersonRole", () => {
  beforeEach(() => configureAuthBearerProvider(() => "test-bearer"));
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("PATCHes /v1/team/{id}/role", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(personFixture({ role: "admin" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await patchPersonRole(IDENTITY, "user_alice" as UserId, {
      role: "admin",
    });

    expect(res.role).toBe("admin");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/team/user_alice/role",
    );
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("PATCH");
  });

  it("surfaces 409 invariant violations", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "cannot_demote_sole_owner" }, 409),
      ),
    );
    await expect(
      patchPersonRole(IDENTITY, "user_owner" as UserId, { role: "member" }),
    ).rejects.toThrow("cannot_demote_sole_owner");
  });
});

describe("offboardPerson", () => {
  beforeEach(() => configureAuthBearerProvider(() => "test-bearer"));
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("POSTs /v1/team/{id}/offboard with reassignments", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(detailFixture(personFixture())),
    );
    vi.stubGlobal("fetch", fetchMock);

    const body: OffboardingRequest = {
      target_user_id: "user_alice" as UserId,
      reassignments: [],
    };
    await offboardPerson(IDENTITY, "user_alice" as UserId, body);

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/team/user_alice/offboard",
    );
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
  });
});

describe("streamTeamEvents", () => {
  it("subscribes via getAppTransport and parses well-formed envelopes", async () => {
    const transport = await import("./transport");
    const subscribeSpy = vi
      .spyOn(transport.getAppTransport(), "subscribeServerSentEvents")
      .mockImplementation((opts) => {
        opts.onMessage(
          JSON.stringify({
            event_id: "evt_1",
            sequence_no: 7,
            event_type: "team.presence_changed",
            person: personFixture(),
            created_at: "2026-05-18T09:00:00Z",
          } satisfies TeamStreamEnvelope),
        );
        opts.onMessage("{not-json");
        return { close: () => undefined };
      });

    const onEvent = vi.fn();
    const onError = vi.fn();
    streamTeamEvents({
      identity: IDENTITY,
      afterSequence: 6,
      onEvent,
      onError,
    });

    expect(subscribeSpy).toHaveBeenCalledTimes(1);
    const opts = subscribeSpy.mock.calls[0][0];
    expect(opts.path).toBe("/v1/team/stream");
    expect(opts.query).toMatchObject({
      org_id: "org_test",
      user_id: "user_test",
      after_sequence: "6",
    });
    expect(opts.eventName).toBe("team_event");

    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(onEvent.mock.calls[0][0]).toMatchObject({
      sequence_no: 7,
      event_type: "team.presence_changed",
    });
    expect(onError).not.toHaveBeenCalled();

    subscribeSpy.mockRestore();
  });

  it("omits after_sequence when the caller has no checkpoint", async () => {
    const transport = await import("./transport");
    const subscribeSpy = vi
      .spyOn(transport.getAppTransport(), "subscribeServerSentEvents")
      .mockImplementation(() => ({ close: () => undefined }));

    streamTeamEvents({
      identity: IDENTITY,
      onEvent: () => undefined,
      onError: () => undefined,
    });

    const opts = subscribeSpy.mock.calls[0][0];
    expect(opts.query).not.toHaveProperty("after_sequence");

    subscribeSpy.mockRestore();
  });

  it("forwards transport-level errors through onError as an Event", async () => {
    const transport = await import("./transport");
    let capturedError: ((err: Error) => void) | undefined;
    const subscribeSpy = vi
      .spyOn(transport.getAppTransport(), "subscribeServerSentEvents")
      .mockImplementation((opts) => {
        capturedError = opts.onError;
        return { close: () => undefined };
      });

    const onError = vi.fn();
    streamTeamEvents({
      identity: IDENTITY,
      onEvent: () => undefined,
      onError,
    });

    expect(capturedError).toBeDefined();
    capturedError!(new Error("network down"));

    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0]).toBeInstanceOf(Event);
    expect((onError.mock.calls[0][0] as Event).type).toBe("error");

    subscribeSpy.mockRestore();
  });
});
