import { render, screen, waitFor } from "@testing-library/react";
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
  NotificationDefaults,
  UserId,
  WebhookSecurityDefaults,
  WorkspaceNotificationDefaults,
} from "@0x-copilot/api-types";

const settingsApiMocks = vi.hoisted(() => ({
  getUserNotificationDefaults: vi.fn(),
  patchUserNotificationDefaults: vi.fn(),
  getWorkspaceNotificationDefaults: vi.fn(),
  patchWorkspaceNotificationDefaults: vi.fn(),
  getWebhookSecurityDefaults: vi.fn(),
  patchWebhookSecurityDefaults: vi.fn(),
}));
vi.mock("../../../api/settingsApi", async () => {
  const actual = await vi.importActual<
    typeof import("../../../api/settingsApi")
  >("../../../api/settingsApi");
  return {
    ...actual,
    ...settingsApiMocks,
  };
});

import { SettingsGateway } from "../SettingsGateway";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

function userDefaults(): NotificationDefaults {
  return {
    user_id: "user_test" as UserId,
    destinations_enabled: { inbox: true, home: true },
    quiet_hours: {
      enabled: false,
      from_local: "22:00",
      to_local: "07:00",
      tz: "UTC",
    },
    updated_at: "2026-05-18T09:00:00Z",
  };
}

function workspaceDefaults(): WorkspaceNotificationDefaults {
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

function webhookSecurity(): WebhookSecurityDefaults {
  return {
    default_hmac_on: true,
    require_ip_allowlist: false,
    max_secret_age_days: 90,
    updated_at: "2026-05-18T09:00:00Z",
    updated_by_user_id: "user_admin" as UserId,
  };
}

describe("SettingsGateway", () => {
  beforeEach(() => {
    Object.values(settingsApiMocks).forEach((m: Mock) => m.mockReset());
  });
  afterEach(() => vi.clearAllMocks());

  it("renders the notification-defaults panel on the matching sub-path", async () => {
    settingsApiMocks.getUserNotificationDefaults.mockResolvedValueOnce(
      userDefaults(),
    );
    settingsApiMocks.getWorkspaceNotificationDefaults.mockResolvedValueOnce(
      workspaceDefaults(),
    );

    render(
      <SettingsGateway
        identity={IDENTITY}
        isAdmin={true}
        subPath="notification-defaults"
        onBackToChat={() => undefined}
      />,
    );

    await waitFor(() => {
      expect(
        screen.queryByTestId("notification-defaults-user-loading"),
      ).toBeNull();
    });
    expect(
      screen.getAllByTestId("notification-defaults-user-row"),
    ).toHaveLength(2);
    expect(
      screen.getAllByTestId("notification-defaults-workspace-row"),
    ).toHaveLength(1);
  });

  it("renders the webhook-security panel on the matching sub-path (admin)", async () => {
    settingsApiMocks.getWebhookSecurityDefaults.mockResolvedValueOnce(
      webhookSecurity(),
    );

    render(
      <SettingsGateway
        identity={IDENTITY}
        isAdmin={true}
        subPath="security-webhooks"
        onBackToChat={() => undefined}
      />,
    );

    await waitFor(() => {
      expect(screen.queryByTestId("webhook-security-loading")).toBeNull();
    });
    const hmacToggle = screen.getByTestId(
      "webhook-security-hmac-toggle",
    ) as HTMLInputElement;
    expect(hmacToggle.checked).toBe(true);
    expect(hmacToggle.disabled).toBe(false);
  });

  it("disables webhook-security inputs for non-admin callers", async () => {
    render(
      <SettingsGateway
        identity={IDENTITY}
        isAdmin={false}
        subPath="security-webhooks"
        onBackToChat={() => undefined}
      />,
    );

    await waitFor(() => {
      expect(screen.queryByTestId("webhook-security-error")).toBeDefined();
    });
    expect(settingsApiMocks.getWebhookSecurityDefaults).not.toHaveBeenCalled();
  });
});
