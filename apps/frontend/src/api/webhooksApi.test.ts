import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  TenantId,
  TriggerId,
  Webhook,
} from "@enterprise-search/api-types";

import { configureAuthBearerProvider } from "./http";
import {
  createWebhook,
  deleteWebhook,
  fetchWebhook,
  fetchWebhooks,
  patchWebhook,
  rotateWebhookSecret,
  testFireWebhook,
} from "./webhooksApi";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

function webhookFixture(overrides: Partial<Webhook> = {}): Webhook {
  return {
    id: "trigger_wh_1" as TriggerId,
    tenant_id: "tenant_1" as TenantId,
    url: "https://example.com/hook",
    secret_strategy: "rotating",
    hmac_algo: "hmac-sha256",
    ip_allowlist: [],
    status: "active",
    last_fire_at: null,
    created_at: "2026-05-18T09:00:00Z",
    rotates_at: "2026-08-16T09:00:00Z",
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

beforeEach(() => {
  configureAuthBearerProvider(() => "test-bearer");
});
afterEach(() => {
  configureAuthBearerProvider(() => null);
  vi.unstubAllGlobals();
});

// ===========================================================================
// LIST + DETAIL
// ===========================================================================

describe("fetchWebhooks + fetchWebhook", () => {
  it("GETs /v1/connectors/webhooks with paging params", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({ items: [webhookFixture()], next_cursor: null }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchWebhooks(IDENTITY, { after: "cursor_w1", limit: 10 });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/connectors/webhooks");
    expect(url).toContain("after=cursor_w1");
    expect(url).toContain("limit=10");
    expect(url).toContain("org_id=org_test");
  });

  it("GETs /v1/connectors/webhooks/{id} with URL-encoded id", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(webhookFixture()));
    vi.stubGlobal("fetch", fetchMock);

    await fetchWebhook(IDENTITY, "trigger_wh_1");

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/connectors/webhooks/trigger_wh_1");
  });
});

// ===========================================================================
// CREATE — copy-once secret reveal
// ===========================================================================

describe("createWebhook", () => {
  it("POSTs /v1/connectors/webhooks and returns the plaintext secret on the envelope", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({
        webhook: webhookFixture(),
        secret_plaintext: "whsec_PLAINTEXT_SECRET_1234",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await createWebhook(IDENTITY, {
      url: "https://example.com/hook",
      secret_strategy: "rotating",
    });

    // The plaintext secret arrives EXACTLY ONCE — the route must surface
    // it through copy-once-reveal and never persist it.
    expect(res.secret_plaintext).toBe("whsec_PLAINTEXT_SECRET_1234");
    expect(res.webhook.id).toBe("trigger_wh_1");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toMatchObject({
      url: "https://example.com/hook",
      secret_strategy: "rotating",
    });
  });

  it("forwards caller-supplied static secret + ip allowlist", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({
        webhook: webhookFixture({
          secret_strategy: "static",
          ip_allowlist: ["10.0.0.0/8"],
        }),
        secret_plaintext: "static-secret",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await createWebhook(IDENTITY, {
      url: "https://example.com/hook",
      secret_strategy: "static",
      secret_plaintext: "user-supplied-static-secret",
      ip_allowlist: ["10.0.0.0/8"],
    });

    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toMatchObject({
      secret_strategy: "static",
      secret_plaintext: "user-supplied-static-secret",
      ip_allowlist: ["10.0.0.0/8"],
    });
  });
});

// ===========================================================================
// PATCH + DELETE
// ===========================================================================

describe("patchWebhook + deleteWebhook", () => {
  it("PATCHes /v1/connectors/webhooks/{id} with the partial body", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(webhookFixture({ status: "paused" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await patchWebhook(IDENTITY, "trigger_wh_1", {
      status: "paused",
    });

    expect(res.status).toBe("paused");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("PATCH");
    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toEqual({ status: "paused" });
  });

  it("DELETEs /v1/connectors/webhooks/{id}", async () => {
    const fetchMock = fetchMockReturning(() => emptyResponse(204));
    vi.stubGlobal("fetch", fetchMock);

    await deleteWebhook(IDENTITY, "trigger_wh_1");

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/connectors/webhooks/trigger_wh_1",
    );
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("DELETE");
  });
});

// ===========================================================================
// ROTATE — copy-once again, plus the grace-secret field
// ===========================================================================

describe("rotateWebhookSecret", () => {
  it("POSTs /v1/connectors/webhooks/{id}/rotate and returns plaintext + grace secret", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({
        webhook: webhookFixture(),
        secret_plaintext: "whsec_NEW_PLAINTEXT",
        grace_secret_plaintext: "whsec_PREVIOUS_PLAINTEXT",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await rotateWebhookSecret(IDENTITY, "trigger_wh_1");

    expect(res.secret_plaintext).toBe("whsec_NEW_PLAINTEXT");
    expect(res.grace_secret_plaintext).toBe("whsec_PREVIOUS_PLAINTEXT");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/connectors/webhooks/trigger_wh_1/rotate",
    );
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
  });

  it("accepts a null grace secret on first rotation", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({
        webhook: webhookFixture(),
        secret_plaintext: "whsec_NEW_PLAINTEXT",
        grace_secret_plaintext: null,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await rotateWebhookSecret(IDENTITY, "trigger_wh_1");
    expect(res.grace_secret_plaintext).toBeNull();
  });
});

// ===========================================================================
// TEST-FIRE
// ===========================================================================

describe("testFireWebhook", () => {
  it("POSTs /v1/connectors/webhooks/{id}/test-fire and surfaces upstream status", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({ response_status: 200, response_ok: true }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await testFireWebhook(IDENTITY, "trigger_wh_1");

    expect(res.response_status).toBe(200);
    expect(res.response_ok).toBe(true);
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/connectors/webhooks/trigger_wh_1/test-fire",
    );
  });

  it("surfaces null response_status on transport failure (timeout / DNS / connection refused)", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({
        response_status: null,
        response_ok: false,
        error: "ConnectError",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await testFireWebhook(IDENTITY, "trigger_wh_1");
    expect(res.response_status).toBeNull();
    expect(res.response_ok).toBe(false);
    expect(res.error).toBe("ConnectError");
  });

  it("propagates 503 facade-unavailable", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "facade_unavailable" }, 503),
      ),
    );
    await expect(testFireWebhook(IDENTITY, "trigger_wh_1")).rejects.toThrow(
      "facade_unavailable",
    );
  });
});
