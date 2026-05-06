// PR 3.5 / G5 — UserCard contract tests.
//
// Asserts the popover behaviour, sign-out wiring, settings invocation,
// and the workspace-switch path that PR 3.5 / G4 fixed (the click must
// reach the prop). The picker fetch is stubbed at the meApi seam so
// the component renders deterministically.

import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement, ReactNode } from "react";

import type { WorkspaceListResponse } from "@enterprise-search/api-types";

const mockListMyWorkspaces = vi.fn<() => Promise<WorkspaceListResponse>>();
const mockGetMyProfile = vi.fn(async () => {
  throw new Error("getMyProfile not configured for this test");
});
vi.mock("../../../../api/meApi", () => ({
  listMyWorkspaces: () => mockListMyWorkspaces(),
  getMyProfile: () => mockGetMyProfile(),
}));

const mockUseAuth = vi.fn();
vi.mock("../../../auth/AuthContext", () => ({
  useAuth: () => mockUseAuth(),
}));

vi.mock("@enterprise-search/design-system", () => ({
  AppIcon: ({ name }: { name: string }) => (
    <span data-testid="appicon">{name}</span>
  ),
  Menu: ({
    open,
    children,
  }: {
    open: boolean;
    children: ReactNode;
    [key: string]: unknown;
  }): ReactElement | null =>
    open ? <div data-testid="user-menu">{children}</div> : null,
}));

import { UserCard } from "./UserCard";

const baseIdentity = {
  org_id: "org_acme",
  user_id: "sarah@acme.com",
  roles: ["admin"],
  display_name: "Sarah Chen",
};

function setIdentity(): void {
  mockUseAuth.mockReturnValue({
    identity: baseIdentity,
    logout: vi.fn(async () => undefined),
  });
}

describe("UserCard", () => {
  it("renders nothing when identity is null", () => {
    mockUseAuth.mockReturnValue({ identity: null, logout: vi.fn() });
    const { container } = render(
      <UserCard onOpenSettings={vi.fn()} onSwitchWorkspace={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("toggles popover on trigger click", async () => {
    setIdentity();
    mockListMyWorkspaces.mockResolvedValue({ workspaces: [] });
    const user = userEvent.setup();
    render(<UserCard onOpenSettings={vi.fn()} onSwitchWorkspace={vi.fn()} />);
    expect(screen.queryByTestId("user-menu")).toBeNull();
    await user.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getByTestId("user-menu")).toBeInTheDocument();
  });

  it("invokes auth.logout when Sign out is clicked", async () => {
    const logout = vi.fn(async () => undefined);
    mockUseAuth.mockReturnValue({
      identity: baseIdentity,
      logout,
    });
    mockListMyWorkspaces.mockResolvedValue({ workspaces: [] });
    const user = userEvent.setup();
    render(<UserCard onOpenSettings={vi.fn()} onSwitchWorkspace={vi.fn()} />);
    await user.click(screen.getByRole("button", { expanded: false }));
    await user.click(screen.getByRole("button", { name: /Sign out/i }));
    expect(logout).toHaveBeenCalledOnce();
  });

  it("invokes onOpenSettings on Settings click", async () => {
    setIdentity();
    mockListMyWorkspaces.mockResolvedValue({ workspaces: [] });
    const onOpenSettings = vi.fn();
    const user = userEvent.setup();
    render(
      <UserCard onOpenSettings={onOpenSettings} onSwitchWorkspace={vi.fn()} />,
    );
    await user.click(screen.getByRole("button", { expanded: false }));
    await user.click(screen.getByRole("button", { name: /Settings/i }));
    expect(onOpenSettings).toHaveBeenCalledOnce();
  });

  // The actual Workspace-switch click (G4) is exercised end-to-end in
  // WorkspacePicker.test.tsx; here we just confirm the popover routes
  // the prop to the picker so consumers can wire it.
  it("forwards onSwitchWorkspace into the WorkspacePicker", async () => {
    setIdentity();
    mockListMyWorkspaces.mockResolvedValue({
      workspaces: [
        {
          org_id: "org_personal",
          display_name: "Personal",
          slug: "personal",
          role: "owner",
          member_count: 1,
          last_active_at: "2026-05-04T08:14:00.000Z",
          is_current: false,
        },
        {
          org_id: "org_acme",
          display_name: "Acme",
          slug: "acme",
          role: "admin",
          member_count: 47,
          last_active_at: "2026-05-05T15:51:02.110Z",
          is_current: true,
        },
      ],
    });
    const onSwitchWorkspace = vi.fn();
    const user = userEvent.setup();
    render(
      <UserCard
        onOpenSettings={vi.fn()}
        onSwitchWorkspace={onSwitchWorkspace}
      />,
    );
    await user.click(screen.getByRole("button", { expanded: false }));
    await waitFor(() =>
      expect(
        screen.getByRole("menuitemradio", { name: /Personal/i }),
      ).toBeInTheDocument(),
    );
    await user.click(screen.getByRole("menuitemradio", { name: /Personal/i }));
    expect(onSwitchWorkspace).toHaveBeenCalledWith("org_personal");
  });
});
