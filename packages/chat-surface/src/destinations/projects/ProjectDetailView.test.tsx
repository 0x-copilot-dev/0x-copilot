import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../providers/RouterProvider";
import type { ArtifactRoute, Router } from "../../routing/router";
import { registerItemRoute, unregisterItemRoute } from "../../refs/registry";
import { formatRelativeTime } from "../../util/time";

import {
  ProjectDetailView,
  type ProjectDetail,
  type ProjectFileRow,
} from "./ProjectDetailView";
import type {
  ChatArchiveRow,
  ConversationId,
  LibraryFileId,
  ProjectId,
} from "@0x-copilot/api-types";

const PROJECT: ProjectDetail = {
  id: "proj-1" as ProjectId,
  name: "Q4 sales push",
  iconEmoji: "🚀",
  colorHue: 220,
  status: "active",
  ownerUserId: "user-owner",
  ownerName: "Sarah Chen",
  memberCount: 5,
};

// A stub router so <ItemLink> (rendered by file rows) has its provider.
function makeRouter(): Router<ArtifactRoute> & {
  navigate: ReturnType<typeof vi.fn>;
} {
  let current: ArtifactRoute | null = null;
  const subscribers = new Set<(r: ArtifactRoute) => void>();
  const navigate = vi.fn((r: ArtifactRoute) => {
    current = r;
    for (const s of subscribers) s(r);
  });
  return {
    current(): ArtifactRoute {
      if (current === null) throw new Error("no route");
      return current;
    },
    navigate,
    subscribe(handler) {
      subscribers.add(handler);
      return () => subscribers.delete(handler);
    },
  };
}

function renderView(
  overrides: Partial<React.ComponentProps<typeof ProjectDetailView>> = {},
  router: Router<ArtifactRoute> = makeRouter(),
) {
  const renderCrossDestinationTab = vi.fn((tab: string, projectId: string) => (
    <div data-testid={`stub-${tab}`} data-project-id={projectId}>
      {tab} stub
    </div>
  ));
  return {
    renderCrossDestinationTab,
    router,
    ...render(
      <RouterProvider router={router}>
        <ProjectDetailView
          project={PROJECT}
          members={[]}
          activity={[]}
          canManage={false}
          renderCrossDestinationTab={renderCrossDestinationTab}
          {...overrides}
        />
      </RouterProvider>,
    ),
  };
}

describe("ProjectDetailView", () => {
  it("renders the header with name, status pill, owner, and member count", () => {
    renderView();
    expect(screen.getByTestId("project-detail-header")).toHaveAttribute(
      "data-project-id",
      "proj-1",
    );
    expect(screen.getByTestId("project-detail-name").textContent).toBe(
      "Q4 sales push",
    );
    expect(screen.getByTestId("project-detail-status")).toHaveAttribute(
      "data-status",
      "active",
    );
    expect(screen.getByTestId("project-detail-owner").textContent).toContain(
      "Sarah Chen",
    );
    expect(screen.getByTestId("project-detail-member-count").textContent).toBe(
      "5 members",
    );
    // v3 (FR-G.5): the header tile shows the name's first letter on the
    // project colour — not the emoji.
    expect(screen.getByTestId("project-detail-icon").textContent).toBe("Q");
    expect(screen.getByTestId("project-detail-icon")).toHaveAttribute(
      "data-color-hue",
      "220",
    );
  });

  it("defaults to the solo profile: no tab bar, .sect-h Chats/Files sections (FR-G.5)", () => {
    renderView();
    expect(screen.getByTestId("project-detail-view")).toHaveAttribute(
      "data-profile",
      "solo",
    );
    expect(screen.queryByTestId("project-detail-tabs")).not.toBeInTheDocument();
    expect(
      screen.getByTestId("project-detail-section-chats"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("project-detail-section-files"),
    ).toBeInTheDocument();
    // Chats section reuses the host's cross-destination slot.
    expect(screen.getByTestId("stub-chats")).toBeInTheDocument();
    // Files section renders the shared files machine (coming-soon by default).
    expect(screen.getByTestId("project-files-tab")).toBeInTheDocument();
  });

  it("renders the Chats/Files section counts when provided (FR-G.5)", () => {
    renderView({
      project: { ...PROJECT, chatCount: 4, fileCount: 2 },
    });
    const chatsSection = screen.getByTestId("project-detail-section-chats");
    const filesSection = screen.getByTestId("project-detail-section-files");
    expect(
      chatsSection.querySelector('[data-testid="section-header-count"]')
        ?.textContent,
    ).toBe("4");
    expect(
      filesSection.querySelector('[data-testid="section-header-count"]')
        ?.textContent,
    ).toBe("2");
  });

  it("renders all eight tabs in order (files after chats) under the team profile", () => {
    renderView({ profile: "team" });
    const tabs = screen.getByTestId("project-detail-tabs");
    const buttons = tabs.querySelectorAll('[role="tab"]');
    const ids = Array.from(buttons).map((b) => b.getAttribute("data-testid"));
    expect(ids).toEqual([
      "project-detail-tab-chats",
      "project-detail-tab-files",
      "project-detail-tab-todos",
      "project-detail-tab-inbox",
      "project-detail-tab-library",
      "project-detail-tab-routines",
      "project-detail-tab-members",
      "project-detail-tab-activity",
    ]);
  });

  it("defaults to the chats tab and calls renderCrossDestinationTab with project id", () => {
    const { renderCrossDestinationTab } = renderView();
    expect(screen.getByTestId("project-detail-view")).toHaveAttribute(
      "data-active-tab",
      "chats",
    );
    expect(screen.getByTestId("stub-chats")).toBeInTheDocument();
    expect(renderCrossDestinationTab).toHaveBeenCalledWith("chats", "proj-1");
  });

  it("switches active tab on click and notifies onTabChange (uncontrolled)", () => {
    const onTabChange = vi.fn();
    renderView({ profile: "team", onTabChange });
    fireEvent.click(screen.getByTestId("project-detail-tab-todos"));
    expect(screen.getByTestId("project-detail-view")).toHaveAttribute(
      "data-active-tab",
      "todos",
    );
    expect(onTabChange).toHaveBeenCalledWith("todos");
    expect(screen.getByTestId("stub-todos")).toBeInTheDocument();
  });

  it("respects the controlled activeTab prop and does not switch internal state", () => {
    const onTabChange = vi.fn();
    renderView({ profile: "team", activeTab: "library", onTabChange });
    expect(screen.getByTestId("project-detail-view")).toHaveAttribute(
      "data-active-tab",
      "library",
    );
    fireEvent.click(screen.getByTestId("project-detail-tab-activity"));
    // Still on library (controlled by parent); but onTabChange fired.
    expect(screen.getByTestId("project-detail-view")).toHaveAttribute(
      "data-active-tab",
      "library",
    );
    expect(onTabChange).toHaveBeenCalledWith("activity");
  });

  it("renders members tab content when active", () => {
    renderView({ profile: "team", initialTab: "members" });
    expect(
      screen.getByTestId("project-detail-panel-members"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("project-members-tab")).toBeInTheDocument();
  });

  it("renders activity tab content when active", () => {
    renderView({ profile: "team", initialTab: "activity" });
    expect(
      screen.getByTestId("project-detail-panel-activity"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("project-activity-tab")).toBeInTheDocument();
  });

  it("renders the transfer-ownership trigger only when canManage and handler are provided", () => {
    const onRequestTransferOwnership = vi.fn();
    const { rerender } = renderView({
      canManage: false,
      onRequestTransferOwnership,
    });
    expect(
      screen.queryByTestId("project-detail-transfer-trigger"),
    ).not.toBeInTheDocument();
    rerender(
      <ProjectDetailView
        project={PROJECT}
        members={[]}
        activity={[]}
        canManage={true}
        renderCrossDestinationTab={() => null}
        onRequestTransferOwnership={onRequestTransferOwnership}
      />,
    );
    const trigger = screen.getByTestId("project-detail-transfer-trigger");
    fireEvent.click(trigger);
    expect(onRequestTransferOwnership).toHaveBeenCalledTimes(1);
  });

  it("formats single-member count without trailing s", () => {
    renderView({ project: { ...PROJECT, memberCount: 1 } });
    expect(screen.getByTestId("project-detail-member-count").textContent).toBe(
      "1 member",
    );
  });

  it("renders the correct status label and tone for paused and archived", () => {
    const { rerender } = renderView({
      project: { ...PROJECT, status: "paused" },
    });
    expect(screen.getByTestId("project-detail-status")).toHaveAttribute(
      "data-status",
      "paused",
    );
    expect(screen.getByTestId("project-detail-status")).toHaveAttribute(
      "data-tone",
      "ready",
    );
    rerender(
      <ProjectDetailView
        project={{ ...PROJECT, status: "archived" }}
        members={[]}
        activity={[]}
        canManage={false}
        renderCrossDestinationTab={() => null}
      />,
    );
    expect(screen.getByTestId("project-detail-status")).toHaveAttribute(
      "data-status",
      "archived",
    );
    expect(screen.getByTestId("project-detail-status")).toHaveAttribute(
      "data-tone",
      "idle",
    );
  });

  it("selecting the files tab renders the files panel", () => {
    renderView({ profile: "team" });
    fireEvent.click(screen.getByTestId("project-detail-tab-files"));
    expect(screen.getByTestId("project-detail-view")).toHaveAttribute(
      "data-active-tab",
      "files",
    );
    expect(
      screen.getByTestId("project-detail-panel-files"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("project-files-tab")).toBeInTheDocument();
  });
});

// ===========================================================================
// Files tab (Phase 4 FR-4.11/4.12/4.13)
// ===========================================================================

const FILES: ReadonlyArray<ProjectFileRow> = [
  {
    id: "file-abc" as LibraryFileId,
    name: "Renewal deck.pdf",
    fileKind: "PDF",
    sizeLabel: "1.2 MB",
    updatedAt: new Date(Date.now() - 5 * 60_000).toISOString(),
  },
  {
    id: "file-def" as LibraryFileId,
    name: "Pricing model.xlsx",
    fileKind: "Dataset",
  },
];

describe("ProjectDetailView — files tab", () => {
  // The library destination owns the `library_file` resolver. Register a
  // stand-in here so the row's <ItemLink> resolves to a real (artifact)
  // route without importing another destination's index at test time.
  beforeAll(() => {
    registerItemRoute(
      "library_file",
      (id) => ({ kind: "workspace", workspaceId: id }),
      { replace: true },
    );
  });
  afterAll(() => {
    unregisterItemRoute("library_file");
  });

  it("degrades to a coming-soon empty state when no files source is provided", () => {
    renderView({ initialTab: "files" }); // `files` prop omitted
    const panel = screen.getByTestId("project-files-tab");
    expect(panel).toHaveAttribute("data-state", "unavailable");
    expect(screen.getByText("Project files coming soon")).toBeInTheDocument();
  });

  it("renders a skeleton while files is null", () => {
    renderView({ initialTab: "files", files: null });
    expect(screen.getByTestId("project-files-tab")).toHaveAttribute(
      "data-state",
      "loading",
    );
    expect(screen.getAllByTestId("project-files-skeleton")).toHaveLength(4);
  });

  it("renders the error state with a working Retry", () => {
    const onRetryFiles = vi.fn();
    renderView({
      initialTab: "files",
      files: { status: "error", error: "boom" },
      onRetryFiles,
    });
    expect(screen.getByTestId("project-files-tab")).toHaveAttribute(
      "data-state",
      "error",
    );
    expect(screen.getByText("boom")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("empty-state-action"));
    expect(onRetryFiles).toHaveBeenCalledTimes(1);
  });

  it("renders the no-files empty state when ready with zero files", () => {
    renderView({ initialTab: "files", files: { status: "ok", data: [] } });
    expect(screen.getByTestId("project-files-tab")).toHaveAttribute(
      "data-state",
      "empty",
    );
    expect(screen.getByText("No files yet")).toBeInTheDocument();
  });

  it("renders one row per file, each wired to a library_file ItemLink ref", async () => {
    renderView({ initialTab: "files", files: { status: "ok", data: FILES } });
    const rows = screen.getAllByTestId("project-file-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveAttribute("data-ref-kind", "library_file");
    expect(rows[0]).toHaveAttribute("data-ref-id", "file-abc");
    expect(rows[1]).toHaveAttribute("data-ref-id", "file-def");
    // The file name is shown as the row's primary text AND as the ItemLink's
    // label now (PRD-04 Seam A: the caller passes the real name, not "File"),
    // so it appears twice per row — assert via the dedicated name-cell testid.
    const names = screen.getAllByTestId("project-file-row-name");
    expect(names.map((n) => n.textContent)).toEqual([
      "Renewal deck.pdf",
      "Pricing model.xlsx",
    ]);
    // <ItemLink> renders synchronously now; its label is the file name.
    const links = screen.getAllByTestId("item-link");
    expect(links).toHaveLength(2);
    expect(links.map((l) => l.textContent)).toEqual([
      "Renewal deck.pdf",
      "Pricing model.xlsx",
    ]);
  });

  it("opens the file's artifact route via the ItemLink on click", async () => {
    const router = makeRouter();
    renderView(
      { initialTab: "files", files: { status: "ok", data: FILES } },
      router,
    );
    const links = await screen.findAllByTestId("item-link");
    expect(links[0]).toHaveAttribute("data-item-kind", "library_file");
    expect(links[0]).toHaveAttribute("data-item-id", "file-abc");
    fireEvent.click(links[0]!);
    expect(router.navigate).toHaveBeenCalledWith({
      kind: "workspace",
      workspaceId: "file-abc",
    });
  });

  it("renders no member/role chips in the files section (solo-safe)", async () => {
    // Solo profile renders Files inline as a `.sect-h` section (no tab panel).
    renderView({ files: { status: "ok", data: FILES } });
    await waitFor(() =>
      expect(screen.getByTestId("project-files-tab")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );
    const section = screen.getByTestId("project-detail-section-files");
    expect(section.querySelector('[data-testid="status-pill"]')).toBeNull();
  });
});

// ===========================================================================
// Chats section (PRD-07 Seam 3 / DoD 10/11).
//
// The solo profile's Chats section is the conversation list filtered by
// project — `ChatArchiveRow`s (PRD-03's `toChatArchiveRow`) carrying the
// `title` / `model` / `updated_at` the old activity-fed list could not
// (`ProjectActivityRecord`, store.py:149-173, has none of them), rendered
// through the shared `_shared/RowList` / `_shared/Row`. Heading count = the
// rendered list length (design copilot-app.jsx:363 — `Chats · {chats.length}`).
// ===========================================================================

const NOW = Date.parse("2026-05-17T12:00:00.000Z");

function chatRow(over: Partial<ChatArchiveRow> = {}): ChatArchiveRow {
  return {
    id: "conv-1" as ConversationId,
    title: "Renewal strategy",
    status: "done",
    preview: "Let's map the Q4 accounts",
    model: "gpt-4o",
    updated_at: "2026-05-17T11:55:00.000Z",
    pinned: false,
    ...over,
  };
}

describe("ProjectDetailView — chats section (PRD-07)", () => {
  it("renders one chat-archive-row per chat carrying title / model / updatedAt (DoD 10)", () => {
    const row = chatRow();
    renderView({ chats: { status: "ok", data: [row] }, now: NOW });
    const section = screen.getByTestId("project-detail-section-chats");
    const rows = within(section).getAllByTestId("chat-archive-row");
    expect(rows).toHaveLength(1);
    const text = rows[0]!.textContent ?? "";
    // The three fields the activity-fed list could not carry.
    expect(text).toContain(row.title);
    expect(text).toContain(row.model);
    expect(text).toContain(formatRelativeTime(row.updated_at, NOW));
  });

  it("the Chats SectionHeader count equals the number of rendered rows (DoD 11a)", () => {
    const rows = [
      chatRow({ id: "conv-a" as ConversationId, title: "Alpha" }),
      chatRow({ id: "conv-b" as ConversationId, title: "Beta" }),
      chatRow({ id: "conv-c" as ConversationId, title: "Gamma" }),
    ];
    renderView({ chats: { status: "ok", data: rows }, now: NOW });
    const section = screen.getByTestId("project-detail-section-chats");
    expect(within(section).getAllByTestId("chat-archive-row")).toHaveLength(3);
    expect(
      within(section).getByTestId("section-header-count").textContent,
    ).toBe("3");
  });

  it("the Files SectionHeader renders the file count sourced from the ready list (DoD 11b)", () => {
    const twelveFiles: ReadonlyArray<ProjectFileRow> = Array.from(
      { length: 12 },
      (_, i) => ({ id: `file-${i}` as LibraryFileId, name: `doc-${i}.pdf` }),
    );
    renderView({ files: { status: "ok", data: twelveFiles }, now: NOW });
    const section = screen.getByTestId("project-detail-section-files");
    expect(
      within(section).getByTestId("section-header-count").textContent,
    ).toBe("12");
  });

  it("opens a chat via onOpenChat when its row is activated", () => {
    const onOpenChat = vi.fn();
    const row = chatRow({ id: "conv-open" as ConversationId });
    renderView({ chats: { status: "ok", data: [row] }, onOpenChat, now: NOW });
    const section = screen.getByTestId("project-detail-section-chats");
    fireEvent.click(within(section).getByTestId("chat-archive-row"));
    expect(onOpenChat).toHaveBeenCalledWith("conv-open");
  });

  it("renders the chats error state with a working Retry", () => {
    const onRetryChats = vi.fn();
    renderView({
      chats: { status: "error", error: "kaboom" },
      onRetryChats,
    });
    const body = screen.getByTestId("project-chats-section-body");
    expect(body).toHaveAttribute("data-state", "error");
    expect(screen.getByText("kaboom")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("empty-state-action"));
    expect(onRetryChats).toHaveBeenCalledTimes(1);
  });

  it("renders the no-chats empty state when ready with zero chats", () => {
    renderView({ chats: { status: "ok", data: [] } });
    expect(screen.getByTestId("project-chats-section-body")).toHaveAttribute(
      "data-state",
      "empty",
    );
    expect(screen.getByText("No chats yet")).toBeInTheDocument();
  });

  it("falls back to the host cross-destination slot when no chats source is wired", () => {
    // `chats` omitted → the solo Chats section defers to the host slot (the
    // team profile always uses that slot), and the heading count comes from the
    // card rollup on `project`.
    renderView({ project: { ...PROJECT, chatCount: 9 } });
    const section = screen.getByTestId("project-detail-section-chats");
    expect(within(section).getByTestId("stub-chats")).toBeInTheDocument();
    expect(
      within(section).getByTestId("section-header-count").textContent,
    ).toBe("9");
  });
});
