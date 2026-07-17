// LibraryDestination shell tests (P7-B1).
//
// Covers: loading skeleton, error/unavailable empty states, ready state
// (PageHeader / Upload primary action / FilterTabs / view-toggle / cards),
// search bar (autofocus + onChange), recently-accessed strip, tutorial
// empty state (3 CTAs), DocList opt-in, search-results slot.

import type {
  ConnectorId,
  ConversationId,
  LibraryDatasetId,
  LibraryFileId,
  LibraryPageId,
  ProjectId,
  RunId,
  SectionResult,
  TenantId,
  UserId,
} from "@0x-copilot/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../providers/RouterProvider";
import type { ArtifactRoute, Router } from "../../routing/router";

// Importing the destination's index registers kinds `"library_file"`,
// `"library_page"`, `"library_dataset"` so the `<ItemLink>` inside each
// card / row resolves without throwing.
import "./index";

// TODO(merge): rewire to "@0x-copilot/api-types"
import type {
  LibraryFileSummary,
  LibraryItemSummary,
  LibraryPageSummary,
  LibraryDatasetSummary,
} from "./_library-stub";

import {
  LibraryDestination,
  type LibraryDestinationProps,
} from "./LibraryDestination";

// ===========================================================================
// Helpers
// ===========================================================================

const asTenantId = (s: string): TenantId => s as unknown as TenantId;
const asUserId = (s: string): UserId => s as unknown as UserId;
const asProjectId = (s: string): ProjectId => s as unknown as ProjectId;
const asConversationId = (s: string): ConversationId =>
  s as unknown as ConversationId;
const asRunId = (s: string): RunId => s as unknown as RunId;
const asFileId = (s: string): LibraryFileId => s as unknown as LibraryFileId;
const asPageId = (s: string): LibraryPageId => s as unknown as LibraryPageId;
const asDatasetId = (s: string): LibraryDatasetId =>
  s as unknown as LibraryDatasetId;
const asConnectorId = (s: string): ConnectorId => s as unknown as ConnectorId;

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

function renderDest(props: LibraryDestinationProps = {}): void {
  const router = makeRouter();
  render(
    <RouterProvider router={router}>
      <LibraryDestination {...props} />
    </RouterProvider>,
  );
}

function ok<T>(data: T): SectionResult<T> {
  return { status: "ok", data };
}

// ===========================================================================
// Fixtures
// ===========================================================================

const NOW = Date.parse("2026-05-17T12:00:00.000Z");

const FILE_A: LibraryFileSummary = {
  kind: "file",
  id: asFileId("lf_alpha"),
  tenant_id: asTenantId("tnt_acme"),
  owner_user_id: asUserId("usr_alice"),
  owner_display_name: "Alice",
  project_id: asProjectId("proj_acme"),
  name: "Q3 forecast.pdf",
  subtitle: "PDF · 2.1 MB",
  source: { kind: "user_upload", uploaded_by: asUserId("usr_alice") },
  tags: ["quarterly"],
  index_status: "indexed",
  index_error: null,
  created_at: "2026-05-15T10:00:00.000Z",
  updated_at: "2026-05-17T09:00:00.000Z",
  last_accessed_at: "2026-05-17T11:00:00.000Z",
  file_kind: "pdf",
  mime: "application/pdf",
  size_bytes: 2_100_000,
};

const PAGE_A: LibraryPageSummary = {
  kind: "page",
  id: asPageId("lp_beta"),
  tenant_id: asTenantId("tnt_acme"),
  owner_user_id: asUserId("usr_alice"),
  project_id: null,
  name: "Renewal playbook",
  subtitle: "What we learned from Q2…",
  source: {
    kind: "agent_save",
    chat_id: asConversationId("conv_1"),
    run_id: asRunId("run_1"),
    message_id: "msg_42",
  },
  tags: [],
  index_status: "indexing",
  index_error: null,
  created_at: "2026-05-14T10:00:00.000Z",
  updated_at: "2026-05-16T10:00:00.000Z",
  last_accessed_at: null,
  version: 3,
};

const DATASET_A: LibraryDatasetSummary = {
  kind: "dataset",
  id: asDatasetId("ld_gamma"),
  tenant_id: asTenantId("tnt_acme"),
  owner_user_id: asUserId("usr_alice"),
  project_id: null,
  name: "Q3 leads.csv",
  subtitle: "4 columns · 1,820 rows",
  source: {
    kind: "connector_sync",
    connector_id: asConnectorId("conn_hubspot"),
    external_id: "ext_1",
  },
  tags: ["leads"],
  index_status: "failed",
  index_error: "embedding endpoint timeout",
  created_at: "2026-05-13T10:00:00.000Z",
  updated_at: "2026-05-13T11:00:00.000Z",
  last_accessed_at: null,
  row_count: 1820,
  column_count: 4,
  format: "csv",
};

const ALL_ROWS: ReadonlyArray<LibraryItemSummary> = [FILE_A, PAGE_A, DATASET_A];

// ===========================================================================
// Tests
// ===========================================================================

describe("LibraryDestination", () => {
  it("renders the loading skeleton when items is null", () => {
    renderDest({ items: null });
    const root = screen.getByTestId("library-destination");
    expect(root).toHaveAttribute("data-state", "loading");
    expect(screen.getAllByTestId("library-skeleton-card")).toHaveLength(6);
  });

  it("auto-focuses the search input on first paint", () => {
    renderDest({ items: ok(ALL_ROWS) });
    const input = screen.getByTestId("library-search-input");
    expect(input).toBe(document.activeElement);
  });

  it("renders the error empty-state with retry", () => {
    const onRetry = vi.fn();
    renderDest({
      items: { status: "error", error: "network down" },
      onRetry,
    });
    expect(screen.getByTestId("library-destination")).toHaveAttribute(
      "data-state",
      "error",
    );
    expect(screen.getByText("Could not load your library")).toBeInTheDocument();
    expect(screen.getByText("network down")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("empty-state-action"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("renders the unavailable empty-state", () => {
    renderDest({ items: { status: "unavailable", error: "feature flag off" } });
    expect(screen.getByTestId("library-destination")).toHaveAttribute(
      "data-state",
      "unavailable",
    );
    expect(screen.getByText("Library unavailable")).toBeInTheDocument();
  });

  it("renders the tutorial card with 3 CTAs when ready with no rows on 'all'", () => {
    const onUploadFile = vi.fn();
    const onNewPage = vi.fn();
    const onConnectSource = vi.fn();
    renderDest({
      items: ok([]),
      onUploadFile,
      onNewPage,
      onConnectSource,
    });
    expect(screen.getByTestId("library-tutorial-card")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("library-tutorial-cta-upload"));
    expect(onUploadFile).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("library-tutorial-cta-new-page"));
    expect(onNewPage).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("library-tutorial-cta-connect"));
    expect(onConnectSource).toHaveBeenCalledTimes(1);
  });

  it("renders the four kind filter tabs (All / Files / Pages / Datasets)", () => {
    renderDest({
      items: ok(ALL_ROWS),
      counts: { all: 3, files: 1, pages: 1, datasets: 1 },
    });
    for (const slug of ["all", "files", "pages", "datasets"]) {
      expect(screen.getByTestId(`filter-tab-${slug}`)).toBeInTheDocument();
    }
  });

  it("fires onFilterChange when a filter tab is clicked", () => {
    const onFilterChange = vi.fn();
    renderDest({ items: ok(ALL_ROWS), onFilterChange });
    fireEvent.click(screen.getByTestId("filter-tab-files"));
    expect(onFilterChange).toHaveBeenCalledWith("files");
  });

  it("renders one library-card per row in CardGrid (default view)", () => {
    renderDest({ items: ok(ALL_ROWS), now: NOW });
    expect(screen.getByTestId("library-destination")).toHaveAttribute(
      "data-view-mode",
      "cards",
    );
    expect(screen.getAllByTestId("library-card")).toHaveLength(3);
    expect(screen.getByTestId("card-grid")).toBeInTheDocument();
  });

  it("renders rows via DocList when viewMode is 'list'", () => {
    renderDest({ items: ok(ALL_ROWS), viewMode: "list", now: NOW });
    expect(screen.getByTestId("library-destination")).toHaveAttribute(
      "data-view-mode",
      "list",
    );
    expect(screen.getByTestId("doc-list")).toBeInTheDocument();
    expect(screen.getAllByTestId("library-row")).toHaveLength(3);
  });

  it("fires onViewModeChange when the view-toggle is flipped", () => {
    const onViewModeChange = vi.fn();
    renderDest({
      items: ok(ALL_ROWS),
      viewMode: "cards",
      onViewModeChange,
    });
    fireEvent.click(screen.getByTestId("library-view-toggle-list"));
    expect(onViewModeChange).toHaveBeenCalledWith("list");
  });

  it("renders the 'Upload' primary action when onUploadFile is supplied", () => {
    const onUploadFile = vi.fn();
    renderDest({ items: ok(ALL_ROWS), onUploadFile });
    const action = screen.getByTestId("page-header-primary-action");
    expect(action).toHaveTextContent("Upload");
    fireEvent.click(action);
    expect(onUploadFile).toHaveBeenCalledTimes(1);
  });

  it("renders the 'New page' secondary action when onNewPage is supplied", () => {
    const onNewPage = vi.fn();
    renderDest({ items: ok(ALL_ROWS), onNewPage });
    fireEvent.click(screen.getByTestId("library-new-page-action"));
    expect(onNewPage).toHaveBeenCalledTimes(1);
  });

  it("renders the recently-accessed strip when on 'all' and recents are supplied", () => {
    renderDest({
      items: ok(ALL_ROWS),
      filter: "all",
      recents: [FILE_A, PAGE_A],
      now: NOW,
    });
    expect(screen.getByTestId("library-recents-strip")).toBeInTheDocument();
    expect(screen.getAllByTestId("library-recents-card")).toHaveLength(2);
  });

  it("suppresses the recently-accessed strip on a kind-filtered view", () => {
    renderDest({
      items: ok([FILE_A]),
      filter: "files",
      recents: [FILE_A, PAGE_A],
      now: NOW,
    });
    expect(
      screen.queryByTestId("library-recents-strip"),
    ).not.toBeInTheDocument();
  });

  it("emits onSearchChange when the user types in the search input", () => {
    const onSearchChange = vi.fn();
    renderDest({ items: ok(ALL_ROWS), onSearchChange });
    fireEvent.change(screen.getByTestId("library-search-input"), {
      target: { value: "renewal" },
    });
    expect(onSearchChange).toHaveBeenCalledWith("renewal");
  });

  it("swaps the body for the search-results slot when searchValue is non-empty", () => {
    const renderSearchResults = vi.fn(({ query }) => (
      <div data-testid="my-search-body">hits for {query}</div>
    ));
    renderDest({
      items: ok(ALL_ROWS),
      searchValue: "renewal",
      renderSearchResults,
    });
    expect(
      screen.getByTestId("library-search-results-slot"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("my-search-body")).toHaveTextContent(
      "hits for renewal",
    );
    // CardGrid body is suppressed while search results are shown.
    expect(screen.queryByTestId("card-grid")).not.toBeInTheDocument();
  });

  it("renders a filter-empty state with 'Clear filters' when filtered view has zero rows", () => {
    const onFilterChange = vi.fn();
    renderDest({
      items: ok([]),
      filter: "pages",
      onFilterChange,
    });
    expect(
      screen.queryByTestId("library-tutorial-card"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("No pages match these filters"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("empty-state-action"));
    expect(onFilterChange).toHaveBeenCalledWith("all");
  });
});
