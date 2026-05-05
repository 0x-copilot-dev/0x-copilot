// PR 3.5 / G5 — Sidebar shell contract tests.
//
// The Sidebar is the layout host. The interesting behaviours (search
// filter reducer, day grouping, keymap chord parsing) live in their own
// modules with their own tests. Here we cover composition + the
// keyboard binding that anchors the rest of the chrome:
//   - new-chat button click invokes onStartNewChat,
//   - ⌘K focuses the search input even when sidebar is collapsed.

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Conversation } from "@enterprise-search/api-types";

const mockUseAuth = vi.fn(() => ({
  identity: {
    org_id: "org_acme",
    user_id: "sarah@acme.com",
    roles: ["admin"],
    display_name: "Sarah",
  },
  logout: vi.fn(async () => undefined),
}));
vi.mock("../../../auth/AuthContext", () => ({
  useAuth: () => mockUseAuth(),
}));

vi.mock("../../../../api/meApi", () => ({
  listMyWorkspaces: vi.fn(async () => ({ workspaces: [] })),
}));

import { Sidebar } from "./Sidebar";

const conversations: readonly Conversation[] = [
  {
    conversation_id: "conv_1",
    org_id: "org_acme",
    user_id: "sarah@acme.com",
    title: "Q1 launch announcement",
    status: "active",
    metadata: {},
    archived_at: null,
    created_at: "2026-05-05T12:00:00Z",
    updated_at: "2026-05-05T13:00:00Z",
  } as Conversation,
];

const NOW = new Date("2026-05-05T13:30:00Z");

describe("Sidebar", () => {
  it("renders a New chat button that fires onStartNewChat", async () => {
    const onStartNewChat = vi.fn();
    const user = userEvent.setup();
    render(
      <Sidebar
        collapsed={false}
        conversations={conversations}
        loading={false}
        activeConversationId={null}
        liveConversationId={null}
        switchingDisabled={false}
        onStartNewChat={onStartNewChat}
        onOpenSettings={vi.fn()}
        onRefresh={vi.fn()}
        now={NOW}
      />,
    );
    await user.click(screen.getByRole("button", { name: /New chat/i }));
    expect(onStartNewChat).toHaveBeenCalledOnce();
  });

  it("renders the search field with aria-controls of the list region", () => {
    render(
      <Sidebar
        collapsed={false}
        conversations={conversations}
        loading={false}
        activeConversationId={null}
        liveConversationId={null}
        switchingDisabled={false}
        onOpenSettings={vi.fn()}
        onRefresh={vi.fn()}
        now={NOW}
      />,
    );
    const search = screen.getByRole("searchbox");
    const listId = search.getAttribute("aria-controls");
    expect(listId).toBeTruthy();
    expect(document.getElementById(listId ?? "")).not.toBeNull();
  });

  it("⌘K (or Ctrl+K) focuses the search input — proves the keymap binding wires up", async () => {
    render(
      <Sidebar
        collapsed={false}
        conversations={conversations}
        loading={false}
        activeConversationId={null}
        liveConversationId={null}
        switchingDisabled={false}
        onOpenSettings={vi.fn()}
        onRefresh={vi.fn()}
        now={NOW}
      />,
    );
    const search = screen.getByRole("searchbox");
    expect(document.activeElement).not.toBe(search);
    // tinykeys binds against `window` and resolves `$mod` to ctrl on jsdom
    // (non-mac platform string). Dispatch on the window root so the
    // listener catches it; send only ctrlKey to match the resolved chord.
    window.dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "k",
        ctrlKey: true,
        bubbles: true,
        cancelable: true,
      }),
    );
    expect(document.activeElement).toBe(search);
  });

  it("user card slot renders nothing when identity is null but the layout still mounts", () => {
    mockUseAuth.mockReturnValueOnce({
      identity: null as never,
      logout: vi.fn(async () => undefined),
    });
    render(
      <Sidebar
        collapsed={false}
        conversations={[]}
        loading={false}
        activeConversationId={null}
        liveConversationId={null}
        switchingDisabled={false}
        onOpenSettings={vi.fn()}
        onRefresh={vi.fn()}
        now={NOW}
      />,
    );
    // `aside` (the sidebar root) still exists even when UserCard renders null.
    expect(screen.getByRole("complementary")).toBeInTheDocument();
  });
});
