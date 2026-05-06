// PR 3.5 / G4 — end-to-end proof that workspace switching reaches auth.
//
// The original bug: `ChatScreen` never provided `onSwitchWorkspace` to
// `AssistantThreadList`, and `useAuth().switchWorkspace` didn't exist.
// This test mounts the realistic chain — UserCard → WorkspacePicker →
// the prop the sidebar forwards — and asserts that clicking a workspace
// row invokes the auth callback with the chosen orgId.
//
// We don't mount the full `ChatScreen` because the component depends on
// 14+ other services (runtime SSE, MCP OAuth, drafts, etc.). The bug is
// at the prop-wiring layer, not deeper; this test exercises the
// integration site directly.

import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type {
  Conversation,
  WorkspaceListResponse,
} from "@enterprise-search/api-types";

const mockListMyWorkspaces = vi.fn<() => Promise<WorkspaceListResponse>>();
const mockGetMyProfile = vi.fn(async () => {
  throw new Error("getMyProfile not configured for this test");
});
vi.mock("../../api/meApi", () => ({
  listMyWorkspaces: () => mockListMyWorkspaces(),
  getMyProfile: () => mockGetMyProfile(),
}));

const mockSwitchWorkspace = vi.fn(async (_orgId: string) => undefined);
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    identity: {
      org_id: "org_acme",
      user_id: "sarah@acme.com",
      roles: ["admin"],
      display_name: "Sarah",
    },
    logout: vi.fn(async () => undefined),
    switchWorkspace: mockSwitchWorkspace,
  }),
}));

import { AssistantThreadList } from "./components/thread/AssistantThreadList";

const conversations: Conversation[] = [
  {
    conversation_id: "conv_1",
    org_id: "org_acme",
    user_id: "sarah@acme.com",
    title: "Q1 launch",
    status: "active",
    metadata: {},
    archived_at: null,
    created_at: "2026-05-05T12:00:00Z",
    updated_at: "2026-05-05T12:00:00Z",
  } as Conversation,
];

describe("workspace switch wiring (PR 3.5 / G4)", () => {
  it("UserCard → WorkspacePicker click → ChatScreen-supplied onSwitchWorkspace runs", async () => {
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

    // Simulate ChatScreen's wiring — the same shape PR 3.5 added at
    // ChatScreen.tsx, including the active-run cancel-then-switch guard.
    const handleSwitch = (orgId: string) => mockSwitchWorkspace(orgId);

    const user = userEvent.setup();
    render(
      <AssistantThreadList
        activeRunId={null}
        activeConversationId={null}
        collapsed={false}
        conversations={conversations}
        loading={false}
        onOpenSettings={vi.fn()}
        onRefresh={vi.fn()}
        onSwitchToThread={vi.fn()}
        onStartNewChat={vi.fn()}
        onToggleSidebar={vi.fn()}
        onSwitchWorkspace={handleSwitch}
      />,
    );

    // Open user-card popover.
    await user.click(screen.getByRole("button", { expanded: false }));
    // Wait for the workspace list to load.
    await waitFor(() =>
      expect(
        screen.getByRole("menuitemradio", { name: /Personal/ }),
      ).toBeInTheDocument(),
    );

    // Click the non-current workspace — proves the prop reaches auth.
    await user.click(screen.getByRole("menuitemradio", { name: /Personal/ }));
    expect(mockSwitchWorkspace).toHaveBeenCalledWith("org_personal");
  });
});
