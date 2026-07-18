import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  Connector,
  ConnectorAuditResponse,
  ConnectorDetailResponse,
  ConnectorId,
  ConnectorListResponse,
  ConnectorScopeEntry,
  ConnectorStreamEnvelope,
  TenantId,
  UserId,
} from "@0x-copilot/api-types";

import {
  completeConnectorOAuth,
  disconnectConnector,
  fetchConnector,
  fetchConnectorAudit,
  fetchConnectors,
  fetchConnectorScopes,
  patchConnectorScopes,
  refreshConnector,
  setConnectorAccessMode,
  startConnectorOAuth,
  streamConnectorEvents,
} from "./connectorsApi";
import { configureAuthBearerProvider } from "./http";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function connectorFixture(overrides: Partial<Connector> = {}): Connector {
  return {
    id: "connector_1" as ConnectorId,
    tenant_id: "tenant_1" as TenantId,
    slug: "gmail",
    display_name: "Gmail",
    description: "Read mail + send drafts.",
    status: "connected",
    owner_user_id: "user_test" as UserId,
    scopes: [
      {
        scope: "gmail.readonly",
        granted: true,
        description: "Read your mail.",
      },
    ],
    last_sync_at: "2026-05-18T08:00:00Z",
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-18T08:00:00Z",
    ...overrides,
  };
}

function listFixture(items: ReadonlyArray<Connector>): ConnectorListResponse {
  return { connectors: items, available: [], next_cursor: null };
}

function detailFixture(connector: Connector): ConnectorDetailResponse {
  return {
    connector,
    consumers: {
      agents: [],
      tools: [],
      projects: [],
      chats_with_grant: 0,
    },
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

// ===========================================================================
// LIST
// ===========================================================================

describe("fetchConnectors", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/connectors with identity and no extras when called bare", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(listFixture([connectorFixture()])),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await fetchConnectors(IDENTITY);

    expect(res.connectors).toHaveLength(1);
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/connectors");
    expect(url).toContain("org_id=org_test");
    expect(url).toContain("user_id=user_test");
    expect(url).not.toContain("filter%5B");
    expect(url).not.toContain("installed=");
    // Facade-only invariant: caller never sees an absolute backend URL.
    expect(url).not.toMatch(/^https?:\/\/(127\.0\.0\.1|localhost):(8000|8100)/);
  });

  it("encodes filter axes + q + cursor + limit + installed", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(listFixture([])));
    vi.stubGlobal("fetch", fetchMock);

    await fetchConnectors(IDENTITY, {
      filters: { status: "expired", slug: "gmail", installed: true },
      q: "mail",
      after: "cursor_xyz",
      limit: 25,
    });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain(encodeURIComponent("filter[status]") + "=expired");
    expect(url).toContain(encodeURIComponent("filter[slug]") + "=gmail");
    expect(url).toContain("installed=true");
    expect(url).toContain("q=mail");
    expect(url).toContain("after=cursor_xyz");
    expect(url).toContain("limit=25");
  });

  it("surfaces server error messages from FastAPI's `detail` envelope", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "tenant_mismatch" }, 403),
      ),
    );
    await expect(fetchConnectors(IDENTITY)).rejects.toThrow("tenant_mismatch");
  });

  it("propagates the 503 facade-unavailable error (Vite proxy timeout / facade down)", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "facade_unavailable" }, 503),
      ),
    );
    await expect(fetchConnectors(IDENTITY)).rejects.toThrow(
      "facade_unavailable",
    );
  });
});

// ===========================================================================
// DETAIL
// ===========================================================================

describe("fetchConnector", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/connectors/{id} with URL-encoded id", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(
        detailFixture(connectorFixture({ id: "conn/1 odd" as ConnectorId })),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await fetchConnector(IDENTITY, "conn/1 odd" as ConnectorId);

    expect(res.connector.id).toBe("conn/1 odd");
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/connectors/conn%2F1%20odd");
  });

  it("propagates 404 as an Error", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "connector_not_found" }, 404),
      ),
    );
    await expect(
      fetchConnector(IDENTITY, "missing" as ConnectorId),
    ).rejects.toThrow("connector_not_found");
  });
});

// ===========================================================================
// OAUTH
// ===========================================================================

describe("startConnectorOAuth + completeConnectorOAuth", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("POSTs /v1/connectors/{slug}/start-oauth with empty body", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({
        authorization_url: "https://example.com/oauth",
        state: "state_abc",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await startConnectorOAuth(IDENTITY, "gmail");

    expect(res.state).toBe("state_abc");
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/connectors/gmail/start-oauth");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
  });

  it("POSTs /v1/connectors/oauth-callback with the callback payload", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(connectorFixture()),
    );
    vi.stubGlobal("fetch", fetchMock);

    await completeConnectorOAuth(IDENTITY, {
      code: "auth_code",
      state: "state_abc",
    });

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/connectors/oauth-callback",
    );
    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toEqual({
      code: "auth_code",
      state: "state_abc",
    });
  });
});

// ===========================================================================
// MUTATIONS
// ===========================================================================

describe("refreshConnector + disconnectConnector + patchConnectorScopes", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("POSTs /v1/connectors/{id}/refresh", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({ connector: connectorFixture() }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await refreshConnector(IDENTITY, "connector_1" as ConnectorId);

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/connectors/connector_1/refresh",
    );
  });

  it("POSTs /v1/connectors/{id}/disconnect", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({
        connector: connectorFixture({ status: "disconnected" }),
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await disconnectConnector(
      IDENTITY,
      "connector_1" as ConnectorId,
    );
    expect(res.connector.status).toBe("disconnected");
  });

  it("PATCHes /v1/connectors/{id}/scopes with the requested scope set", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({
        reauth_url: "https://example.com/reauth",
        state: "state_xyz",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const scopes: ConnectorScopeEntry[] = [
      {
        scope: "gmail.readonly",
        granted: true,
        description: "Read your mail.",
      },
    ];
    const res = await patchConnectorScopes(
      IDENTITY,
      "connector_1" as ConnectorId,
      { scopes },
    );

    expect(res.reauth_url).toBe("https://example.com/reauth");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("PATCH");
    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toEqual({ scopes });
  });

  it("PATCHes /v1/connectors/{id}/access-mode with the chosen mode", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({
        connector: connectorFixture({ access_mode: "read_act" }),
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await setConnectorAccessMode(
      IDENTITY,
      "connector_1" as ConnectorId,
      { access_mode: "read_act" },
    );

    expect(res.connector.access_mode).toBe("read_act");
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/connectors/connector_1/access-mode");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("PATCH");
    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toEqual({ access_mode: "read_act" });
  });
});

// ===========================================================================
// AUDIT + SCOPES
// ===========================================================================

describe("fetchConnectorAudit + fetchConnectorScopes", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/connectors/{id}/audit with paging params", async () => {
    const response: ConnectorAuditResponse = {
      entries: [],
      next_cursor: null,
    };
    const fetchMock = fetchMockReturning(() => jsonResponse(response));
    vi.stubGlobal("fetch", fetchMock);

    await fetchConnectorAudit(IDENTITY, "connector_1" as ConnectorId, {
      after: "cursor_a3",
      limit: 50,
      since: "2026-04-01T00:00:00Z",
    });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/connectors/connector_1/audit");
    expect(url).toContain("after=cursor_a3");
    expect(url).toContain("limit=50");
    expect(url).toContain(
      "since=" + encodeURIComponent("2026-04-01T00:00:00Z"),
    );
  });

  it("propagates 403 when caller is not admin (audit is admin-only)", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() => jsonResponse({ detail: "admin_required" }, 403)),
    );
    await expect(
      fetchConnectorAudit(IDENTITY, "connector_1" as ConnectorId),
    ).rejects.toThrow("admin_required");
  });

  it("GETs /v1/connectors/{id}/scopes", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse({ scopes: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await fetchConnectorScopes(IDENTITY, "connector_1" as ConnectorId);

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/connectors/connector_1/scopes",
    );
  });
});

// ===========================================================================
// SSE — envelope parsing + transport seam
// ===========================================================================

describe("streamConnectorEvents", () => {
  it("subscribes via getAppTransport and parses well-formed envelopes", async () => {
    const transport = await import("./transport");
    const subscribeSpy = vi
      .spyOn(transport.getAppTransport(), "subscribeServerSentEvents")
      .mockImplementation((opts) => {
        // Synchronously hand back two messages: one valid, one malformed.
        opts.onMessage(
          JSON.stringify({
            event_id: "evt_1",
            sequence_no: 7,
            event_type: "connector.status_changed",
            created_at: "2026-05-18T09:00:00Z",
          } satisfies ConnectorStreamEnvelope),
        );
        // Malformed JSON — should be silently dropped, not delivered to
        // onEvent, and not tear down the connection.
        opts.onMessage("{not-json");
        // Unknown event_type — also dropped by the guard.
        opts.onMessage(
          JSON.stringify({
            event_id: "evt_2",
            sequence_no: 8,
            event_type: "connector.unrecognised_kind",
            created_at: "2026-05-18T09:00:01Z",
          }),
        );
        return { close: () => undefined };
      });

    const onEvent = vi.fn();
    const onError = vi.fn();

    streamConnectorEvents({
      identity: IDENTITY,
      afterSequence: 6,
      onEvent,
      onError,
    });

    expect(subscribeSpy).toHaveBeenCalledTimes(1);
    const opts = subscribeSpy.mock.calls[0][0];
    expect(opts.path).toBe("/v1/connectors/stream");
    expect(opts.query).toMatchObject({
      org_id: "org_test",
      user_id: "user_test",
      after_sequence: "6",
    });
    expect(opts.eventName).toBe("connector_event");

    // Only the well-formed envelope is delivered to onEvent.
    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(onEvent.mock.calls[0][0]).toMatchObject({
      sequence_no: 7,
      event_type: "connector.status_changed",
    });
    // The malformed frame did not propagate to onError — the connection
    // stays open.
    expect(onError).not.toHaveBeenCalled();

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
    streamConnectorEvents({
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

  it("omits after_sequence when the caller has no checkpoint", async () => {
    const transport = await import("./transport");
    const subscribeSpy = vi
      .spyOn(transport.getAppTransport(), "subscribeServerSentEvents")
      .mockImplementation(() => ({ close: () => undefined }));

    streamConnectorEvents({
      identity: IDENTITY,
      onEvent: () => undefined,
      onError: () => undefined,
    });

    const opts = subscribeSpy.mock.calls[0][0];
    expect(opts.query).not.toHaveProperty("after_sequence");

    subscribeSpy.mockRestore();
  });
});
