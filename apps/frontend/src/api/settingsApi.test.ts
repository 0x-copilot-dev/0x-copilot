import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  NotificationDefaults,
  UserId,
  WebhookSecurityDefaults,
  WorkspaceNotificationDefaults,
} from "@enterprise-search/api-types";

import { configureAuthBearerProvider } from "./http";
import {
  getUserNotificationDefaults,
  getWebhookSecurityDefaults,
  getWorkspaceNotificationDefaults,
  patchUserNotificationDefaults,
  patchWebhookSecurityDefaults,
  patchWorkspaceNotificationDefaults,
} from "./settingsApi";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

function userDefaultsFixture(): NotificationDefaults {
  return {
    user_id: "user_test" as UserId,
    destinations_enabled: { inbox: true, home: true },
    quiet_hours: {
      enabled: false,
      from_local: "22:00",
      to_local: "07:00",
      tz: "America/Los_Angeles",
    },
    updated_at: "2026-05-18T09:00:00Z",
  };
}

function workspaceDefaultsFixture(): WorkspaceNotificationDefaults {
  return {
    destinations_enabled: { inbox: true },
    quiet_hours: {
      enabled: true,
      from_local: "22:00",
      to_local: "07:00",
      tz: "UTC",
    },
    updated_at: "2026-05-18T09:00:00Z",
    updated_by_user_id: "user_admin" as UserId,
  };
}

function webhookSecurityFixture(): WebhookSecurityDefaults {
  return {
    default_hmac_on: true,
    require_ip_allowlist: false,
    max_secret_age_days: 90,
    updated_at: "2026-05-18T09:00:00Z",
    updated_by_user_id: "user_admin" as UserId,
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

describe("user notification defaults", () => {
  beforeEach(() => configureAuthBearerProvider(() => "test-bearer"));
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/settings/notifications", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(userDefaultsFixture()),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await getUserNotificationDefaults(IDENTITY);
    expect(res.user_id).toBe("user_test");
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/settings/notifications");
    expect(url).toContain("org_id=org_test");
    expect(url).not.toMatch(/^https?:\/\/(127\.0\.0\.1|localhost):(8000|8100)/);
  });

  it("PATCHes /v1/settings/notifications with partial body", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(userDefaultsFixture()),
    );
    vi.stubGlobal("fetch", fetchMock);
    await patchUserNotificationDefaults(IDENTITY, {
      destinations_enabled: { inbox: false },
    });
    const call = fetchMock.mock.calls[0];
    expect(String(call[0])).toContain("/v1/settings/notifications");
    expect((call[1] as RequestInit).method).toBe("PATCH");
  });

  it("propagates 503 as an Error", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "settings unavailable" }, 503),
      ),
    );
    await expect(getUserNotificationDefaults(IDENTITY)).rejects.toThrow(
      "settings unavailable",
    );
  });
});

describe("workspace notification defaults", () => {
  beforeEach(() => configureAuthBearerProvider(() => "test-bearer"));
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/settings/workspace/notifications", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(workspaceDefaultsFixture()),
    );
    vi.stubGlobal("fetch", fetchMock);
    const res = await getWorkspaceNotificationDefaults(IDENTITY);
    expect(res.updated_by_user_id).toBe("user_admin");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/settings/workspace/notifications",
    );
  });

  it("PATCHes /v1/settings/workspace/notifications", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(workspaceDefaultsFixture()),
    );
    vi.stubGlobal("fetch", fetchMock);
    await patchWorkspaceNotificationDefaults(IDENTITY, {
      destinations_enabled: { inbox: true },
    });
    const call = fetchMock.mock.calls[0];
    expect((call[1] as RequestInit).method).toBe("PATCH");
  });

  it("surfaces 403 for non-admin callers", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() => jsonResponse({ detail: "admin_required" }, 403)),
    );
    await expect(getWorkspaceNotificationDefaults(IDENTITY)).rejects.toThrow(
      "admin_required",
    );
  });
});

describe("webhook security defaults", () => {
  beforeEach(() => configureAuthBearerProvider(() => "test-bearer"));
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/settings/security/webhooks", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(webhookSecurityFixture()),
    );
    vi.stubGlobal("fetch", fetchMock);
    const res = await getWebhookSecurityDefaults(IDENTITY);
    expect(res.default_hmac_on).toBe(true);
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/settings/security/webhooks",
    );
  });

  it("PATCHes /v1/settings/security/webhooks", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(webhookSecurityFixture()),
    );
    vi.stubGlobal("fetch", fetchMock);
    await patchWebhookSecurityDefaults(IDENTITY, {
      default_hmac_on: false,
      max_secret_age_days: 30,
    });
    const call = fetchMock.mock.calls[0];
    expect(String(call[0])).toContain("/v1/settings/security/webhooks");
    expect((call[1] as RequestInit).method).toBe("PATCH");
    expect(JSON.parse((call[1] as RequestInit).body as string)).toMatchObject({
      default_hmac_on: false,
      max_secret_age_days: 30,
    });
  });
});
