import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  NotificationDefaults,
  WorkspaceNotificationDefaults,
} from "@enterprise-search/api-types";

import { NotificationsPage } from "./NotificationsPage";

const USER_ID = "user_test" as unknown as NotificationDefaults["user_id"];

const MY: NotificationDefaults = {
  user_id: USER_ID,
  destinations_enabled: {
    chats: true,
    runs: true,
    approvals: true,
    inbox: true,
    routines: true,
    library: true,
    agents: true,
    tools: true,
    connectors: true,
    team: true,
    memory: true,
  },
  quiet_hours: {
    enabled: false,
    from_local: "22:00",
    to_local: "07:00",
    tz: "America/Los_Angeles",
  },
  updated_at: "2026-05-18T00:00:00Z",
};

const WS: WorkspaceNotificationDefaults = {
  destinations_enabled: { chats: true, runs: true },
  quiet_hours: {
    enabled: false,
    from_local: "22:00",
    to_local: "07:00",
    tz: "UTC",
  },
  updated_at: "2026-05-18T00:00:00Z",
  updated_by_user_id: null,
};

describe("<NotificationsPage>", () => {
  it("renders only the My-defaults body when isAdmin=false (no tablist)", () => {
    render(
      <NotificationsPage
        myDefaults={MY}
        workspaceDefaults={null}
        isAdmin={false}
        onSaveMy={() => undefined}
      />,
    );
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.getByTestId("notify-toggle-chats")).toBeInTheDocument();
  });

  it("renders tabs and switches between My / Workspace when isAdmin=true", () => {
    render(
      <NotificationsPage
        myDefaults={MY}
        workspaceDefaults={WS}
        isAdmin={true}
        onSaveMy={() => undefined}
        onSaveWorkspace={() => undefined}
      />,
    );
    const tablist = screen.getByRole("tablist", {
      name: "Notification defaults scope",
    });
    expect(tablist).toBeInTheDocument();
    // Switch to workspace defaults.
    fireEvent.click(screen.getByTestId("filter-tab-workspace"));
    // Workspace defaults has only chats+runs in its initial blob —
    // so the row for "memory" should still be rendered (missing → true),
    // but the chats/runs toggles read from WS.
    expect(screen.getByTestId("notify-toggle-chats")).toBeInTheDocument();
  });

  it("onSaveMy patch only carries changed fields (destinations only)", () => {
    const onSaveMy = vi.fn();
    render(
      <NotificationsPage
        myDefaults={MY}
        workspaceDefaults={null}
        isAdmin={false}
        onSaveMy={onSaveMy}
      />,
    );
    // Flip just "chats" off.
    fireEvent.click(screen.getByTestId("notify-toggle-chats"));
    fireEvent.click(screen.getByTestId("notifications-save"));
    expect(onSaveMy).toHaveBeenCalledTimes(1);
    const patch = onSaveMy.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(patch).toEqual({ destinations_enabled: { chats: false } });
    // quiet_hours did not change → not in the patch.
    expect(patch.quiet_hours).toBeUndefined();
  });

  it("onSaveMy does not fire when nothing changed", () => {
    const onSaveMy = vi.fn();
    render(
      <NotificationsPage
        myDefaults={MY}
        workspaceDefaults={null}
        isAdmin={false}
        onSaveMy={onSaveMy}
      />,
    );
    fireEvent.click(screen.getByTestId("notifications-save"));
    expect(onSaveMy).not.toHaveBeenCalled();
  });

  it("uses fieldsets with legends for ARIA grouping", () => {
    render(
      <NotificationsPage
        myDefaults={MY}
        workspaceDefaults={null}
        isAdmin={false}
        onSaveMy={() => undefined}
      />,
    );
    expect(
      screen.getByRole("group", { name: "Notify me about" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("group", { name: "Quiet hours" }),
    ).toBeInTheDocument();
  });

  it("admin tab is hidden from non-admins (no admin chrome leak)", () => {
    render(
      <NotificationsPage
        myDefaults={MY}
        workspaceDefaults={WS}
        isAdmin={false}
        onSaveMy={() => undefined}
        onSaveWorkspace={() => undefined}
      />,
    );
    expect(screen.queryByTestId("filter-tab-workspace")).toBeNull();
  });
});
