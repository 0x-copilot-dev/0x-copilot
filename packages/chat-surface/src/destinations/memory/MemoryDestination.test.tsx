// MemoryDestination shell tests (P12-B2).
//
// Covers: unwired state (placeholder copy), loading skeleton, error
// state with retry, ready state row rendering, kind & scope filter
// chips, search input forwarding, ItemLink rendering for memories that
// carry a project_id, ARIA roles.

import type {
  MemoryItem,
  MemoryItemId,
  ProjectId,
  SectionResult,
  TenantId,
  UserId,
} from "@0x-copilot/api-types";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../providers/RouterProvider";
import type { ArtifactRoute, Router } from "../../routing/router";

// Importing the destination's index runs its `registerIfAbsent(...)`
// calls as a side-effect; registers kind `"memory"`. Also pulls the
// project resolver in via the projects destination — and for tests
// that don't import projects, the project_id chip falls back to the
// deleted-chip path which is acceptable (we only assert ARIA presence).
import "./index";

import {
  MemoryDestination,
  type MemoryDestinationProps,
  type MemoryKindFilterSlug,
} from "./MemoryDestination";

// ===========================================================================
// Helpers
// ===========================================================================

const asMemoryId = (s: string): MemoryItemId => s as unknown as MemoryItemId;
const asTenantId = (s: string): TenantId => s as unknown as TenantId;
const asUserId = (s: string): UserId => s as unknown as UserId;
const asProjectId = (s: string): ProjectId => s as unknown as ProjectId;

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
      if (current === null) {
        return { kind: "workspace", workspaceId: "mem" };
      }
      return current;
    },
    navigate,
    subscribe(handler) {
      subscribers.add(handler);
      return () => subscribers.delete(handler);
    },
  };
}

function renderDest(props: MemoryDestinationProps = {}): void {
  const router = makeRouter();
  render(
    <RouterProvider router={router}>
      <MemoryDestination {...props} />
    </RouterProvider>,
  );
}

const NOW = Date.parse("2026-05-17T12:00:00.000Z");

type MemoryInit = Omit<Partial<MemoryItem>, "id"> & { readonly id: string };

function makeMemory(over: MemoryInit): MemoryItem {
  return {
    id: asMemoryId(over.id),
    tenant_id: asTenantId("tnt_1"),
    scope: over.scope ?? "user",
    kind: over.kind ?? "fact",
    title: over.title ?? "I'm a Python developer",
    body: over.body ?? "Prefer Python 3.13 idioms.",
    tags: over.tags ?? [],
    created_by: over.created_by ?? {
      kind: "user",
      id: asUserId("usr_self") as unknown as string,
    },
    last_used_at: over.last_used_at === undefined ? null : over.last_used_at,
    created_at: over.created_at ?? "2026-05-01T00:00:00.000Z",
    updated_at: over.updated_at ?? "2026-05-16T00:00:00.000Z",
    project_id: over.project_id ?? null,
  };
}

function ok<T>(data: T): SectionResult<T> {
  return { status: "ok", data };
}

// ===========================================================================
// Tests
// ===========================================================================

describe("MemoryDestination", () => {
  it("renders the unwired-state placeholder when items is undefined", () => {
    renderDest({});
    const root = screen.getByTestId("memory-destination");
    expect(root).toHaveAttribute("data-state", "unwired");
    expect(root).toHaveAttribute("aria-label", "Memory destination");
    expect(screen.getByTestId("page-header-title")).toHaveTextContent(/Memory/);
    expect(screen.getByTestId("empty-state-title")).toHaveTextContent(
      /what the agent remembers/i,
    );
  });

  it("renders the loading skeleton when items is null", () => {
    renderDest({ items: null });
    expect(screen.getByTestId("memory-destination")).toHaveAttribute(
      "data-state",
      "loading",
    );
    expect(screen.getAllByTestId("memory-skeleton-row").length).toBeGreaterThan(
      0,
    );
  });

  it("renders whole-list error state with a retry button when status=error", () => {
    const onRetry = vi.fn();
    renderDest({
      items: { status: "error", error: "Boom" },
      onRetry,
    });
    expect(screen.getByTestId("memory-destination")).toHaveAttribute(
      "data-state",
      "error",
    );
    expect(screen.getByTestId("empty-state")).toHaveTextContent(
      /could not load memory/i,
    );
    fireEvent.click(screen.getByTestId("empty-state-action"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("renders an empty state when status=ok with no rows + Add memory CTA", () => {
    const onCreate = vi.fn();
    renderDest({
      items: ok<ReadonlyArray<MemoryItem>>([]),
      onCreateMemory: onCreate,
    });
    expect(screen.getByTestId("empty-state-title")).toHaveTextContent(
      /no memory yet/i,
    );
    fireEvent.click(screen.getByTestId("empty-state-action"));
    expect(onCreate).toHaveBeenCalledTimes(1);
  });

  it("renders one row per memory item with kind chip + title + scope chip", () => {
    const items: ReadonlyArray<MemoryItem> = [
      makeMemory({ id: "mem_1", kind: "skill", title: "Speaks Python" }),
      makeMemory({
        id: "mem_2",
        kind: "preference",
        scope: "workspace",
        title: "Sign off as 'Best, Parth'",
      }),
    ];
    renderDest({ items: ok<ReadonlyArray<MemoryItem>>(items), now: NOW });
    const rows = screen.getAllByTestId("memory-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveAttribute("data-memory-kind", "skill");
    expect(rows[0]).toHaveAttribute("data-memory-scope", "user");
    expect(rows[1]).toHaveAttribute("data-memory-scope", "workspace");
    expect(rows[0]).toHaveTextContent(/Speaks Python/);
    expect(rows[0]).toHaveTextContent(/Skill/);
    expect(rows[1]).toHaveTextContent(/Workspace/);
  });

  it("renders the kind FilterTabs with All / Skills / Facts / Preferences and fires onFilterChange", () => {
    const onFilterChange = vi.fn<(slug: MemoryKindFilterSlug) => void>();
    renderDest({
      items: ok<ReadonlyArray<MemoryItem>>([]),
      onFilterChange,
    });
    // Tab strip presence
    const kindTablist = screen.getByRole("tablist", {
      name: /memory kind filter/i,
    });
    expect(kindTablist).toBeInTheDocument();
    // Slugs present — scoped to the kind tablist (the scope tablist
    // also has an "all" slug with the same data-testid).
    for (const slug of ["all", "skill", "fact", "preference"] as const) {
      expect(
        kindTablist.querySelector(`[data-testid="filter-tab-${slug}"]`),
      ).not.toBeNull();
    }
    // `filter-tab-skill` is unique to the kind tablist.
    fireEvent.click(screen.getByTestId("filter-tab-skill"));
    expect(onFilterChange).toHaveBeenCalledWith("skill");
  });

  it("renders the scope sub-filter (All / My / Workspace) and fires onScopeFilterChange", () => {
    const onScopeFilterChange = vi.fn();
    renderDest({
      items: ok<ReadonlyArray<MemoryItem>>([]),
      onScopeFilterChange,
    });
    expect(
      screen.getByRole("tablist", { name: /memory scope filter/i }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("filter-tab-workspace"));
    expect(onScopeFilterChange).toHaveBeenCalledWith("workspace");
  });

  it("fires onSearch as the search input changes", () => {
    const onSearch = vi.fn();
    renderDest({
      items: ok<ReadonlyArray<MemoryItem>>([]),
      onSearch,
    });
    const input = screen.getByTestId("memory-search-input");
    fireEvent.change(input, { target: { value: "python" } });
    expect(onSearch).toHaveBeenLastCalledWith("python");
  });

  it("renders an ItemLink for a memory whose project_id is set", async () => {
    const items = [
      makeMemory({
        id: "mem_proj",
        title: "Acme onboarding",
        project_id: asProjectId("proj_acme"),
      }),
    ];
    renderDest({ items: ok<ReadonlyArray<MemoryItem>>(items), now: NOW });
    // The ItemLink renders synchronously (PRD-04): an anchor when a project
    // route is registered, else inert `item-link-static` text carrying the
    // caller's label. No route is registered here → the static span is present.
    const row = screen.getByTestId("memory-row");
    const linked = row.querySelector(
      '[data-testid="item-link"], [data-testid="item-link-static"]',
    );
    expect(linked).not.toBeNull();
  });

  it("renders the detail-slot in place of the list when focusedMemoryId is set", () => {
    const renderDetail = vi.fn(({ memoryId }) => (
      <div data-testid="custom-detail">{`detail:${memoryId}`}</div>
    ));
    const items = [makeMemory({ id: "mem_focus", title: "Focused" })];
    renderDest({
      items: ok<ReadonlyArray<MemoryItem>>(items),
      renderDetail,
      focusedMemoryId: asMemoryId("mem_focus"),
    });
    expect(screen.getByTestId("memory-detail-slot")).toBeInTheDocument();
    expect(screen.getByTestId("custom-detail")).toHaveTextContent(
      "detail:mem_focus",
    );
    // The list body is replaced — no rows.
    expect(screen.queryAllByTestId("memory-row")).toHaveLength(0);
  });
});
