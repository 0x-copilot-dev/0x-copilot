// ChatsArchiveRoute integration tests (Phase 4 · PR-4.3).
//
// Drives the host binder with a MOCKED chatsApi + agentApi (no real
// transport) and asserts the FR-4.7 / FR-4.8 / FR-4.9 wiring:
//   * loading  → destination skeleton (`data-state="loading"`),
//   * error    → Retry empty-state, and Retry refetches,
//   * empty    → per-view empty copy,
//   * ready    → bucketed rows,
//   * row click → onOpenRun(conversationId)  (reopen → Run),
//   * New chat  → createConversation, then onOpenRun(newId)  (open Run).

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ChatArchiveRow,
  ChatsArchive as ChatsArchiveData,
  Conversation,
  ConversationId,
  SectionResult,
} from "@0x-copilot/api-types";

// Mock the archive binder so the tests don't drive the real fetch/mapping —
// that projection surface is covered in chatsApi's own unit tests.
const chatsApiMocks = vi.hoisted(() => ({
  fetchChatsArchive: vi.fn(),
}));
vi.mock("./api/chatsApi", () => ({
  fetchChatsArchive: chatsApiMocks.fetchChatsArchive,
}));

// Mock the conversation-create call the New-chat path uses.
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

// Resolves through the mocks above.
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

function archive(
  overrides: Partial<ChatsArchiveData> = {},
): SectionResult<ChatsArchiveData> {
  return {
    status: "ok",
    data: { pinned: [], recent: [], archived: [], ...overrides },
  };
}

const emptyArchive: SectionResult<ChatsArchiveData> = archive();

function pending<T>(): Promise<T> {
  return new Promise<T>(() => {
    /* never resolves — holds the loading state */
  });
}

describe("ChatsArchiveRoute", () => {
  beforeEach(() => {
    chatsApiMocks.fetchChatsArchive.mockReset();
    agentApiMocks.createConversation.mockReset();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the loading skeleton while the archive is in flight", () => {
    chatsApiMocks.fetchChatsArchive.mockReturnValueOnce(
      pending<SectionResult<ChatsArchiveData>>(),
    );

    render(<ChatsArchiveRoute identity={IDENTITY} onOpenRun={vi.fn()} />);

    expect(screen.getByTestId("chats-archive")).toHaveAttribute(
      "data-state",
      "loading",
    );
    expect(screen.getByTestId("chats-skeleton")).toBeInTheDocument();
  });

  it("renders the error empty-state and refetches on Retry", async () => {
    chatsApiMocks.fetchChatsArchive
      .mockResolvedValueOnce({ status: "error", error: "boom" })
      .mockResolvedValueOnce(archive({ recent: [makeRow()] }));

    render(<ChatsArchiveRoute identity={IDENTITY} onOpenRun={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByTestId("chats-error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("empty-state-body").textContent).toContain(
      "boom",
    );

    fireEvent.click(screen.getByTestId("empty-state-action")); // Retry

    await waitFor(() => {
      expect(screen.getByTestId("chats-sections")).toBeInTheDocument();
    });
    expect(chatsApiMocks.fetchChatsArchive).toHaveBeenCalledTimes(2);
  });

  it("renders the empty copy when the archive has no conversations", async () => {
    chatsApiMocks.fetchChatsArchive.mockResolvedValueOnce(emptyArchive);

    render(<ChatsArchiveRoute identity={IDENTITY} onOpenRun={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByTestId("chats-empty")).toBeInTheDocument();
    });
  });

  it("renders bucketed rows once the archive resolves", async () => {
    chatsApiMocks.fetchChatsArchive.mockResolvedValueOnce(
      archive({
        pinned: [makeRow({ id: asConversationId("conv-pin"), pinned: true })],
        recent: [makeRow({ id: asConversationId("conv-recent") })],
      }),
    );

    render(<ChatsArchiveRoute identity={IDENTITY} onOpenRun={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getAllByTestId("chat-archive-row")).toHaveLength(2);
    });
    expect(screen.getByTestId("chats-section-pinned")).toBeInTheDocument();
    expect(screen.getByTestId("chats-section-recent")).toBeInTheDocument();
  });

  it("navigates to Run when a row is clicked (reopen → Run)", async () => {
    const onOpenRun = vi.fn();
    chatsApiMocks.fetchChatsArchive.mockResolvedValueOnce(
      archive({ recent: [makeRow({ id: asConversationId("conv-42") })] }),
    );

    render(<ChatsArchiveRoute identity={IDENTITY} onOpenRun={onOpenRun} />);

    await waitFor(() => {
      expect(screen.getByTestId("chat-archive-row")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("chat-archive-row"));

    expect(onOpenRun).toHaveBeenCalledTimes(1);
    expect(onOpenRun).toHaveBeenCalledWith("conv-42");
  });

  it("creates a conversation then opens Run when New chat is clicked", async () => {
    const onOpenRun = vi.fn();
    chatsApiMocks.fetchChatsArchive.mockResolvedValueOnce(
      archive({ recent: [makeRow()] }),
    );
    agentApiMocks.createConversation.mockResolvedValueOnce({
      conversation_id: "conv-new",
    } as Conversation);

    render(<ChatsArchiveRoute identity={IDENTITY} onOpenRun={onOpenRun} />);

    await waitFor(() => {
      expect(screen.getByTestId("chats-sections")).toBeInTheDocument();
    });
    // "New chat" moved off the PageHeader primary action: it now lives on the
    // Pinned section header (chat-surface `chats-new-chat`, FR-G.3).
    fireEvent.click(screen.getByTestId("chats-new-chat"));

    await waitFor(() => {
      expect(agentApiMocks.createConversation).toHaveBeenCalledWith(IDENTITY);
    });
    await waitFor(() => {
      expect(onOpenRun).toHaveBeenCalledWith("conv-new");
    });
  });
});
