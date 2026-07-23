// ChatsArchiveRoute integration tests (PRD-09 — post-collapse onto the D1 hook).
//
// The route no longer fetches: the shared `useChatsArchive` controller
// (@0x-copilot/chat-surface) owns the read/write model. These tests mock that
// controller so the route's remaining responsibilities are asserted in
// isolation: it feeds the destination the controller's `archive`, wires reopen,
// wires New chat (createConversation → onOpenRun), and shows the New-chat error
// banner.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ChatArchiveRow,
  ChatsArchive as ChatsArchiveData,
  Conversation,
  ConversationId,
  SectionResult,
} from "@0x-copilot/api-types";

// Mock ONLY the controller; keep the real `ChatsArchive` presentation component.
const controllerMock = vi.hoisted(() => ({
  archive: null as SectionResult<ChatsArchiveData> | null,
  hasMore: { pinned: false, recent: false, archived: false },
  onLoadMore: vi.fn(),
  onTogglePin: vi.fn(),
  onToggleArchive: vi.fn(),
  retry: vi.fn(),
}));
vi.mock("@0x-copilot/chat-surface", async () => {
  const actual = await vi.importActual<
    typeof import("@0x-copilot/chat-surface")
  >("@0x-copilot/chat-surface");
  return { ...actual, useChatsArchive: () => controllerMock };
});

// Mock the conversation-create call the New-chat path uses; keep pinConversation
// real (migrateLegacyPins imports it — harmless in tests with no legacy key).
const agentApiMocks = vi.hoisted(() => ({
  createConversation: vi.fn(),
}));
vi.mock("../../api/agentApi", async () => {
  const actual =
    await vi.importActual<typeof import("../../api/agentApi")>(
      "../../api/agentApi",
    );
  return { ...actual, createConversation: agentApiMocks.createConversation };
});

import { ChatsArchiveRoute } from "./ChatsArchiveRoute";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

const asConversationId = (value: string): ConversationId =>
  value as ConversationId;

function makeRow(overrides: Partial<ChatArchiveRow> = {}): ChatArchiveRow {
  return {
    id: asConversationId("conv-1"),
    title: "Quarterly plan",
    status: "done",
    preview: "Draft the Q3 rollout",
    model: "gpt-4o",
    updated_at: "2026-07-18T11:00:00Z",
    pinned: false,
    ...overrides,
  };
}

function ok(
  overrides: Partial<ChatsArchiveData> = {},
): SectionResult<ChatsArchiveData> {
  return {
    status: "ok",
    data: { pinned: [], recent: [], archived: [], ...overrides },
  };
}

describe("ChatsArchiveRoute", () => {
  beforeEach(() => {
    controllerMock.archive = null;
    controllerMock.onTogglePin.mockReset();
    controllerMock.retry.mockReset();
    agentApiMocks.createConversation.mockReset();
  });
  afterEach(() => vi.clearAllMocks());

  it("renders the loading skeleton while the controller archive is null", () => {
    controllerMock.archive = null;
    render(<ChatsArchiveRoute identity={IDENTITY} onOpenRun={vi.fn()} />);
    expect(screen.getByTestId("chats-archive")).toHaveAttribute(
      "data-state",
      "loading",
    );
  });

  it("renders bucketed rows from the controller archive", () => {
    controllerMock.archive = ok({
      pinned: [makeRow({ id: asConversationId("conv-pin"), pinned: true })],
      recent: [makeRow({ id: asConversationId("conv-recent") })],
    });
    render(<ChatsArchiveRoute identity={IDENTITY} onOpenRun={vi.fn()} />);
    expect(screen.getAllByTestId("chat-archive-row")).toHaveLength(2);
    expect(screen.getByTestId("chats-section-pinned")).toBeInTheDocument();
    expect(screen.getByTestId("chats-section-recent")).toBeInTheDocument();
  });

  it("navigates to Run when a row is clicked (reopen → Run)", () => {
    const onOpenRun = vi.fn();
    controllerMock.archive = ok({
      recent: [makeRow({ id: asConversationId("conv-42") })],
    });
    render(<ChatsArchiveRoute identity={IDENTITY} onOpenRun={onOpenRun} />);
    fireEvent.click(screen.getByTestId("chat-archive-row"));
    expect(onOpenRun).toHaveBeenCalledWith("conv-42");
  });

  it("creates a conversation then opens Run when New chat is clicked", async () => {
    const onOpenRun = vi.fn();
    controllerMock.archive = ok({ recent: [makeRow()] });
    agentApiMocks.createConversation.mockResolvedValueOnce({
      conversation_id: "conv-new",
    } as Conversation);

    render(<ChatsArchiveRoute identity={IDENTITY} onOpenRun={onOpenRun} />);
    fireEvent.click(screen.getByTestId("chats-new-chat"));

    await waitFor(() =>
      expect(agentApiMocks.createConversation).toHaveBeenCalledWith(IDENTITY),
    );
    await waitFor(() => expect(onOpenRun).toHaveBeenCalledWith("conv-new"));
  });
});
