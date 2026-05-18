// TeamPanel — left rail filters + invite CTA.

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TeamPanel } from "./TeamPanel";

describe("TeamPanel", () => {
  it("renders role + presence sections as tablists", () => {
    render(<TeamPanel />);
    const role = screen.getByRole("tablist", {
      name: "Team filter — role (panel)",
    });
    expect(role).toBeInTheDocument();
    expect(within(role).getByTestId("filter-tab-all")).toBeInTheDocument();
    expect(within(role).getByTestId("filter-tab-admins")).toBeInTheDocument();

    const presence = screen.getByRole("tablist", {
      name: "Team filter — presence (panel)",
    });
    expect(presence).toBeInTheDocument();
    expect(within(presence).getByTestId("filter-tab-any")).toBeInTheDocument();
    expect(
      within(presence).getByTestId("filter-tab-active"),
    ).toBeInTheDocument();
    expect(
      within(presence).getByTestId("filter-tab-in_meeting"),
    ).toBeInTheDocument();
  });

  it("fires onRoleFilterChange when a role tab is clicked", () => {
    const onRoleFilterChange = vi.fn();
    render(<TeamPanel onRoleFilterChange={onRoleFilterChange} />);
    const role = screen.getByRole("tablist", {
      name: "Team filter — role (panel)",
    });
    fireEvent.click(within(role).getByTestId("filter-tab-guests"));
    expect(onRoleFilterChange).toHaveBeenCalledWith("guests");
  });

  it("fires onPresenceFilterChange when a presence tab is clicked", () => {
    const onPresenceFilterChange = vi.fn();
    render(<TeamPanel onPresenceFilterChange={onPresenceFilterChange} />);
    const presence = screen.getByRole("tablist", {
      name: "Team filter — presence (panel)",
    });
    fireEvent.click(within(presence).getByTestId("filter-tab-away"));
    expect(onPresenceFilterChange).toHaveBeenCalledWith("away");
  });

  it("renders Invite CTA when canInvite + onInvite are wired", () => {
    const onInvite = vi.fn();
    render(<TeamPanel canInvite={true} onInvite={onInvite} />);
    fireEvent.click(screen.getByTestId("context-panel-primary-action"));
    expect(onInvite).toHaveBeenCalledTimes(1);
  });

  it("hides Invite CTA when canInvite is false", () => {
    render(<TeamPanel canInvite={false} onInvite={vi.fn()} />);
    expect(
      screen.queryByTestId("context-panel-primary-action"),
    ).not.toBeInTheDocument();
  });
});
