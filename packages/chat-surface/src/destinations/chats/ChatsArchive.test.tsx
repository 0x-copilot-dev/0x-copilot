// ChatsArchive component tests (Phase 4 · PR-4.2).
//
// Covers the FR-4.2 4-state machine (loading / error+Retry / empty /
// ready), the pinned/recent/archived section rendering + empty-section
// hiding (FR-4.5), the row shape (title / status chip / preview / mono
// model / mono time — FR-4.6), reopen on click AND Enter/Space
// (FR-4.7), and the "New chat" callback (FR-4.8).

import type {
  ChatArchiveRow,
  ChatsArchive as ChatsArchiveData,
  ConversationId,
  SectionResult,
} from "@0x-copilot/api-types";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChatsArchive, type ChatsArchiveProps } from "./ChatsArchive";

// Pinned reference instant so relative-time output is deterministic.
const NOW = Date.parse("2026-07-18T12:00:00Z");

const asConversationId = (s: string): ConversationId =>
  s as unknown as ConversationId;

function makeRow(
  overrides: Partial<Omit<ChatArchiveRow, "id">> & { id?: string } = {},
): ChatArchiveRow {
  return {
    id: asConversationId(overrides.id ?? "conv-1"),
    title: overrides.title ?? "Quarterly revenue analysis",
    status: overrides.status ?? "done",
    preview: overrides.preview ?? "Here is the summary you asked for…",
    model: overrides.model ?? "gpt-4o",
    updated_at: overrides.updated_at ?? "2026-07-18T11:00:00Z",
    pinned: overrides.pinned ?? false,
  };
}

function okArchive(
  archive: Partial<ChatsArchiveData>,
): SectionResult<ChatsArchiveData> {
  return {
    status: "ok",
    data: {
      pinned: archive.pinned ?? [],
      recent: archive.recent ?? [],
      archived: archive.archived ?? [],
    },
  };
}

function renderArchive(props: Partial<ChatsArchiveProps> = {}): {
  onReopen: ReturnType<typeof vi.fn>;
  onNewChat: ReturnType<typeof vi.fn>;
  onRetry: ReturnType<typeof vi.fn>;
} {
  const onReopen = vi.fn();
  const onNewChat = vi.fn();
  const onRetry = vi.fn();
  render(
    <ChatsArchive
      archive={props.archive}
      onReopen={props.onReopen ?? onReopen}
      onNewChat={props.onNewChat ?? onNewChat}
      onRetry={props.onRetry ?? onRetry}
      now={props.now ?? NOW}
    />,
  );
  return { onReopen, onNewChat, onRetry };
}

// ---------------------------------------------------------------------------
// 4-state machine (FR-4.2)
// ---------------------------------------------------------------------------

describe("ChatsArchive — states", () => {
  it("renders a loading skeleton when archive is null", () => {
    renderArchive({ archive: null });
    const root = screen.getByTestId("chats-archive");
    expect(root).toHaveAttribute("data-state", "loading");
    expect(screen.getByTestId("chats-skeleton")).toBeInTheDocument();
    expect(screen.getAllByTestId("chats-skeleton-row").length).toBeGreaterThan(
      0,
    );
    // "New chat" CTA is available even while loading.
    expect(screen.getByTestId("page-header-primary-action")).toHaveTextContent(
      "New chat",
    );
  });

  it("renders an error state with a Retry action wired to onRetry", () => {
    const { onRetry } = renderArchive({
      archive: { status: "error", error: "Boom" },
    });
    const root = screen.getByTestId("chats-archive");
    expect(root).toHaveAttribute("data-state", "error");
    const errorNode = screen.getByTestId("chats-error");
    expect(errorNode).toHaveAttribute("role", "alert");
    expect(within(errorNode).getByText("Boom")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("empty-state-action"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("renders an unavailable state distinct from error", () => {
    renderArchive({ archive: { status: "unavailable" } });
    expect(screen.getByTestId("chats-archive")).toHaveAttribute(
      "data-state",
      "unavailable",
    );
    expect(screen.getByTestId("empty-state-title")).toHaveTextContent(
      /unavailable/i,
    );
  });

  it("renders the empty copy + New chat CTA when there are no conversations", () => {
    const { onNewChat } = renderArchive({ archive: okArchive({}) });
    const root = screen.getByTestId("chats-archive");
    expect(root).toHaveAttribute("data-state", "empty");
    expect(screen.getByTestId("empty-state-title")).toHaveTextContent(
      "Start your first run",
    );
    fireEvent.click(screen.getByTestId("empty-state-action"));
    expect(onNewChat).toHaveBeenCalledTimes(1);
  });

  it("renders the ready section list when there are rows", () => {
    renderArchive({
      archive: okArchive({ recent: [makeRow()] }),
    });
    expect(screen.getByTestId("chats-archive")).toHaveAttribute(
      "data-state",
      "ready",
    );
    expect(screen.getByTestId("chats-sections")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Sections (FR-4.5) — pinned / recent / archived, empty hidden
// ---------------------------------------------------------------------------

describe("ChatsArchive — sections", () => {
  it("renders pinned / recent / archived sections in order", () => {
    renderArchive({
      archive: okArchive({
        pinned: [makeRow({ id: "p1", title: "Pinned thread", pinned: true })],
        recent: [makeRow({ id: "r1", title: "Recent thread" })],
        archived: [
          makeRow({ id: "a1", title: "Archived thread", status: "archived" }),
        ],
      }),
    });
    expect(screen.getByTestId("chats-section-pinned")).toBeInTheDocument();
    expect(screen.getByTestId("chats-section-recent")).toBeInTheDocument();
    expect(screen.getByTestId("chats-section-archived")).toBeInTheDocument();

    const sections = screen.getAllByTestId(
      /^chats-section-(pinned|recent|archived)$/,
    );
    expect(sections.map((el) => el.getAttribute("data-section-key"))).toEqual([
      "pinned",
      "recent",
      "archived",
    ]);
  });

  it("hides empty sections", () => {
    renderArchive({
      archive: okArchive({ recent: [makeRow()] }),
    });
    expect(screen.getByTestId("chats-section-recent")).toBeInTheDocument();
    expect(screen.queryByTestId("chats-section-pinned")).toBeNull();
    expect(screen.queryByTestId("chats-section-archived")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Row shape (FR-4.6)
// ---------------------------------------------------------------------------

describe("ChatsArchive — row", () => {
  it("shows title, status chip, preview, mono model, and relative time", () => {
    renderArchive({
      archive: okArchive({
        recent: [
          makeRow({
            title: "Weekly digest",
            status: "running",
            preview: "Compiling the digest…",
            model: "claude-opus-4",
            updated_at: "2026-07-18T11:30:00Z",
          }),
        ],
      }),
    });
    const row = screen.getByTestId("chat-archive-row");
    expect(row).toHaveAttribute("data-status", "running");
    expect(within(row).getByTestId("chat-archive-row-title")).toHaveTextContent(
      "Weekly digest",
    );
    expect(
      within(row).getByTestId("chat-archive-row-preview"),
    ).toHaveTextContent("Compiling the digest…");
    expect(within(row).getByTestId("chat-archive-row-model")).toHaveTextContent(
      "claude-opus-4",
    );
    // running → jade/ok tone chip.
    expect(within(row).getByTestId("status-pill")).toHaveAttribute(
      "data-status",
      "ok",
    );
    // relative time rendered from the ISO string (30m before NOW).
    expect(
      within(row).getByTestId("chat-archive-row-time").textContent ?? "",
    ).not.toHaveLength(0);
  });

  it("omits the model tag when the model is unknown (empty string)", () => {
    renderArchive({
      archive: okArchive({ recent: [makeRow({ model: "" })] }),
    });
    expect(screen.queryByTestId("chat-archive-row-model")).toBeNull();
  });

  it("maps paused → warning and done/archived → muted tones", () => {
    renderArchive({
      archive: okArchive({
        pinned: [makeRow({ id: "p", status: "paused" })],
        recent: [makeRow({ id: "r", status: "done" })],
        archived: [makeRow({ id: "a", status: "archived" })],
      }),
    });
    const rows = screen.getAllByTestId("chat-archive-row");
    const toneFor = (status: string): string | null => {
      const row = rows.find((r) => r.getAttribute("data-status") === status);
      return (
        within(row!)
          .getAllByTestId("status-pill")[0]
          ?.getAttribute("data-status") ?? null
      );
    };
    expect(toneFor("paused")).toBe("warning");
    expect(toneFor("done")).toBe("muted");
    expect(toneFor("archived")).toBe("muted");
  });
});

// ---------------------------------------------------------------------------
// Callbacks (FR-4.7 / FR-4.8)
// ---------------------------------------------------------------------------

describe("ChatsArchive — callbacks", () => {
  it("invokes onReopen with the conversation id on click", () => {
    const { onReopen } = renderArchive({
      archive: okArchive({ recent: [makeRow({ id: "conv-42" })] }),
    });
    fireEvent.click(screen.getByTestId("chat-archive-row"));
    expect(onReopen).toHaveBeenCalledTimes(1);
    expect(onReopen).toHaveBeenCalledWith("conv-42");
  });

  it("invokes onReopen when Enter is pressed on a focused row", () => {
    const { onReopen } = renderArchive({
      archive: okArchive({ recent: [makeRow({ id: "conv-7" })] }),
    });
    fireEvent.keyDown(screen.getByTestId("chat-archive-row"), { key: "Enter" });
    expect(onReopen).toHaveBeenCalledWith("conv-7");
  });

  it("invokes onReopen when Space is pressed on a focused row", () => {
    const { onReopen } = renderArchive({
      archive: okArchive({ recent: [makeRow({ id: "conv-9" })] }),
    });
    fireEvent.keyDown(screen.getByTestId("chat-archive-row"), { key: " " });
    expect(onReopen).toHaveBeenCalledWith("conv-9");
  });

  it("does not reopen on an unrelated key", () => {
    const { onReopen } = renderArchive({
      archive: okArchive({ recent: [makeRow()] }),
    });
    fireEvent.keyDown(screen.getByTestId("chat-archive-row"), { key: "a" });
    expect(onReopen).not.toHaveBeenCalled();
  });

  it("invokes onNewChat from the header primary action", () => {
    const { onNewChat } = renderArchive({
      archive: okArchive({ recent: [makeRow()] }),
    });
    fireEvent.click(screen.getByTestId("page-header-primary-action"));
    expect(onNewChat).toHaveBeenCalledTimes(1);
  });

  it("marks each row focusable and labelled for reopen", () => {
    renderArchive({
      archive: okArchive({ recent: [makeRow({ title: "Budget review" })] }),
    });
    const row = screen.getByTestId("chat-archive-row");
    expect(row).toHaveAttribute("role", "button");
    expect(row).toHaveAttribute("tabindex", "0");
    expect(row).toHaveAttribute("aria-label", "Reopen Budget review");
  });
});
