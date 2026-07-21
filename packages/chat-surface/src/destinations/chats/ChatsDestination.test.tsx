// ChatsDestination tests (Phase 4 · PR-4.2 recast).
//
// The destination is now a thin pure-presentation wrapper around
// `ChatsArchive` — no ChatsSidebar, no thread-canvas placeholder. The
// exhaustive state/section/row/callback coverage lives in
// `ChatsArchive.test.tsx`; here we assert the wrapper forwards props and
// stays safe with no props (loading) so it can mount before its PR-4.3
// host binder is wired.

import type {
  ChatArchiveRow,
  ChatsArchive as ChatsArchiveData,
  ConversationId,
  SectionResult,
} from "@0x-copilot/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChatsDestination } from "./ChatsDestination";

const asConversationId = (s: string): ConversationId =>
  s as unknown as ConversationId;

function row(id: string, title: string): ChatArchiveRow {
  return {
    id: asConversationId(id),
    title,
    status: "done",
    preview: "…",
    model: "gpt-4o",
    updated_at: "2026-07-18T11:00:00Z",
    pinned: false,
  };
}

function ok(recent: ChatArchiveRow[]): SectionResult<ChatsArchiveData> {
  return { status: "ok", data: { pinned: [], recent, archived: [] } };
}

describe("ChatsDestination", () => {
  it("renders the loading archive when mounted with no props", () => {
    render(<ChatsDestination />);
    const root = screen.getByTestId("chats-archive");
    expect(root).toHaveAttribute("data-state", "loading");
    // No legacy thread-canvas placeholder anymore.
    expect(screen.queryByTestId("thread-canvas-placeholder")).toBeNull();
  });

  it("forwards archive data through to ChatsArchive", () => {
    render(<ChatsDestination archive={ok([row("c1", "Forwarded thread")])} />);
    expect(screen.getByTestId("chats-archive")).toHaveAttribute(
      "data-state",
      "ready",
    );
    expect(screen.getByText("Forwarded thread")).toBeInTheDocument();
  });

  it("forwards onReopen and onNewChat callbacks", () => {
    const onReopen = vi.fn();
    const onNewChat = vi.fn();
    render(
      <ChatsDestination
        archive={ok([row("c9", "Thread")])}
        onReopen={onReopen}
        onNewChat={onNewChat}
      />,
    );
    fireEvent.click(screen.getByTestId("chat-archive-row"));
    expect(onReopen).toHaveBeenCalledWith("c9");
    // "New chat" now lives on the Pinned section header (FR-G.3).
    fireEvent.click(screen.getByTestId("chats-new-chat"));
    expect(onNewChat).toHaveBeenCalledTimes(1);
  });
});
