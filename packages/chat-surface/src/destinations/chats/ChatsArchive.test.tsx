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
    // The surface opens with the `.pg-lead` intro (no 22px page title).
    expect(screen.getByTestId("chats-lead")).toHaveTextContent(
      "Every conversation with the agent",
    );
    expect(screen.queryByTestId("page-header")).toBeNull();
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

  it("hides empty Recent/Archived, but keeps Pinned (it hosts New chat)", () => {
    renderArchive({
      archive: okArchive({ recent: [makeRow()] }),
    });
    expect(screen.getByTestId("chats-section-recent")).toBeInTheDocument();
    // Pinned always renders in the ready state — it carries the "＋ New chat"
    // primary (FR-G.3) — but shows no rowlist when it has no rows.
    expect(screen.getByTestId("chats-section-pinned")).toBeInTheDocument();
    expect(screen.queryByTestId("chats-section-pinned-list")).toBeNull();
    expect(screen.getByTestId("chats-new-chat")).toBeInTheDocument();
    expect(screen.queryByTestId("chats-section-archived")).toBeNull();
  });

  it("labels the archived section 'Archived · history' (FR-G.3)", () => {
    renderArchive({
      archive: okArchive({
        archived: [makeRow({ id: "a1", status: "archived" })],
      }),
    });
    const archived = screen.getByTestId("chats-section-archived");
    expect(
      within(archived).getByTestId("section-header-label"),
    ).toHaveTextContent("Archived · history");
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

  it("renders the row gracefully when preview + model are both empty", () => {
    renderArchive({
      archive: okArchive({ recent: [makeRow({ preview: "", model: "" })] }),
    });
    const row = screen.getByTestId("chat-archive-row");
    expect(within(row).getByTestId("chat-archive-row-title")).toHaveTextContent(
      "Quarterly revenue analysis",
    );
    // No sub-line parts.
    expect(screen.queryByTestId("chat-archive-row-preview")).toBeNull();
    expect(screen.queryByTestId("chat-archive-row-model")).toBeNull();
    expect(within(row).queryByTestId("row-sub")).toBeNull();
  });

  it("shows the brand mark for a live row and the chats glyph otherwise (FR-G.3)", () => {
    renderArchive({
      archive: okArchive({
        recent: [
          makeRow({ id: "live", status: "running" }),
          makeRow({ id: "done", status: "done" }),
        ],
      }),
    });
    const rows = screen.getAllByTestId("chat-archive-row");
    const liveRow = rows.find(
      (r) => r.getAttribute("data-status") === "running",
    )!;
    const doneRow = rows.find((r) => r.getAttribute("data-status") === "done")!;

    const liveIcon = within(liveRow).getByTestId("chat-archive-row-icon");
    expect(liveIcon).toHaveAttribute("data-live", "true");
    expect(liveIcon.querySelector('svg[viewBox="0 0 400 400"]')).not.toBeNull();

    const doneIcon = within(doneRow).getByTestId("chat-archive-row-icon");
    expect(doneIcon).toHaveAttribute("data-live", "false");
    expect(doneIcon.querySelector('svg[viewBox="0 0 24 24"]')).not.toBeNull();
  });

  it("shows the status dot on a LIVE chip only (FR-G.3)", () => {
    renderArchive({
      archive: okArchive({
        recent: [
          makeRow({ id: "live", status: "running" }),
          makeRow({ id: "done", status: "done" }),
        ],
      }),
    });
    const rows = screen.getAllByTestId("chat-archive-row");
    const dotIn = (status: string): boolean => {
      const rowEl = rows.find((r) => r.getAttribute("data-status") === status)!;
      const pill = within(rowEl).getByTestId("status-pill");
      return pill.querySelector('span[aria-hidden="true"]') !== null;
    };
    expect(dotIn("running")).toBe(true);
    expect(dotIn("done")).toBe(false);
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
    // done → success (jade) per the PRD-B design schema (was grey/muted).
    expect(toneFor("done")).toBe("ok");
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

  it("invokes onNewChat from the Pinned-header primary action (FR-G.3)", () => {
    const { onNewChat } = renderArchive({
      archive: okArchive({ recent: [makeRow()] }),
    });
    // "New chat" lives on the Pinned section header, not a top-right button.
    const pinned = screen.getByTestId("chats-section-pinned");
    fireEvent.click(within(pinned).getByTestId("chats-new-chat"));
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
