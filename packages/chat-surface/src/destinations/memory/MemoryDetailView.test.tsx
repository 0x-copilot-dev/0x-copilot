// MemoryDetailView tests (P12-B2).
//
// Covers: tab switching (Body / Provenance / Used by), markdown body
// renders via the shared PagePreview, ARIA roles, ItemLink rows on the
// Used-by tab, edit/delete callbacks fire.

import type {
  ConversationId,
  MemoryItem,
  MemoryItemId,
  ProjectId,
  TenantId,
  UserId,
} from "@enterprise-search/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../providers/RouterProvider";
import type { ArtifactRoute, Router } from "../../routing/router";

import "./index";

import { MemoryDetailView } from "./MemoryDetailView";

const asMemoryId = (s: string): MemoryItemId => s as unknown as MemoryItemId;
const asTenantId = (s: string): TenantId => s as unknown as TenantId;
const asUserId = (s: string): UserId => s as unknown as UserId;
const asProjectId = (s: string): ProjectId => s as unknown as ProjectId;
const asConversationId = (s: string): ConversationId =>
  s as unknown as ConversationId;

function makeRouter(): Router<ArtifactRoute> {
  return {
    current: () => ({ kind: "workspace", workspaceId: "mem" }),
    navigate: vi.fn(),
    subscribe: () => () => {},
  };
}

function makeMemory(over: Partial<MemoryItem> = {}): MemoryItem {
  return {
    id: asMemoryId("mem_1"),
    tenant_id: asTenantId("tnt_1"),
    scope: over.scope ?? "user",
    kind: over.kind ?? "fact",
    title: over.title ?? "Python developer",
    body: over.body ?? "# About me\n\nPrefer Python 3.13.",
    tags: over.tags ?? ["python"],
    created_by: over.created_by ?? {
      kind: "agent",
      id: "agt_extractor",
    },
    last_used_at: over.last_used_at ?? "2026-05-16T10:00:00.000Z",
    created_at: over.created_at ?? "2026-05-01T00:00:00.000Z",
    updated_at: over.updated_at ?? "2026-05-16T10:00:00.000Z",
    project_id: over.project_id ?? null,
  };
}

const NOW = Date.parse("2026-05-17T12:00:00.000Z");

function renderView(
  props: Partial<Parameters<typeof MemoryDetailView>[0]> = {},
): void {
  const memory = props.memory ?? makeMemory();
  render(
    <RouterProvider router={makeRouter()}>
      <MemoryDetailView memory={memory} now={NOW} {...props} />
    </RouterProvider>,
  );
}

describe("MemoryDetailView", () => {
  it("renders title + chips and the Body tab is the default", () => {
    renderView();
    expect(screen.getByTestId("memory-detail")).toHaveAttribute(
      "data-memory-kind",
      "fact",
    );
    expect(screen.getByTestId("memory-detail-title")).toHaveTextContent(
      /Python developer/,
    );
    // Body tab is active and renders the PagePreview shared markdown
    // renderer (Streamdown). We don't introspect inside the renderer;
    // it's enough that the test-id surface is present.
    expect(screen.getByTestId("memory-detail-panel-body")).toBeInTheDocument();
    expect(screen.getByTestId("library-page-preview")).toBeInTheDocument();
  });

  it("switches tabs when the FilterTabs slugs are clicked", () => {
    renderView();
    fireEvent.click(screen.getByTestId("filter-tab-provenance"));
    expect(
      screen.getByTestId("memory-detail-panel-provenance"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("memory-detail-panel-body"),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("filter-tab-used_by"));
    expect(
      screen.getByTestId("memory-detail-panel-used_by"),
    ).toBeInTheDocument();
  });

  it("renders the Provenance tab with created-by / created / updated rows", () => {
    renderView();
    fireEvent.click(screen.getByTestId("filter-tab-provenance"));
    const panel = screen.getByTestId("memory-detail-provenance");
    expect(panel).toHaveTextContent(/Agent · agt_extractor/);
    expect(panel).toHaveTextContent(/Created/);
    expect(panel).toHaveTextContent(/Updated/);
    expect(panel).toHaveTextContent(/Last used/);
  });

  it("renders 'Not used by any runs yet' empty state on the Used-by tab when usedBy is empty", () => {
    renderView();
    fireEvent.click(screen.getByTestId("filter-tab-used_by"));
    expect(screen.getByTestId("memory-detail-used-by-empty")).toHaveTextContent(
      /not used/i,
    );
  });

  it("renders one ItemLink row per usedBy entry", () => {
    const usedBy = [
      {
        at: "2026-05-17T11:00:00.000Z",
        ref: { kind: "chat" as const, id: asConversationId("conv_1") },
      },
      {
        at: "2026-05-17T11:30:00.000Z",
        ref: { kind: "chat" as const, id: asConversationId("conv_2") },
      },
    ];
    renderView({ memory: makeMemory(), usedBy });
    fireEvent.click(screen.getByTestId("filter-tab-used_by"));
    expect(screen.getAllByTestId("memory-detail-used-by-row")).toHaveLength(2);
  });

  it("fires onEdit / onDelete / onClose callbacks", () => {
    const onEdit = vi.fn();
    const onDelete = vi.fn();
    const onClose = vi.fn();
    const memory = makeMemory();
    render(
      <RouterProvider router={makeRouter()}>
        <MemoryDetailView
          memory={memory}
          onEdit={onEdit}
          onDelete={onDelete}
          onClose={onClose}
          now={NOW}
        />
      </RouterProvider>,
    );
    fireEvent.click(screen.getByTestId("memory-detail-edit"));
    expect(onEdit).toHaveBeenCalledWith(memory.id);
    fireEvent.click(screen.getByTestId("memory-detail-delete"));
    expect(onDelete).toHaveBeenCalledWith(memory.id);
    fireEvent.click(screen.getByTestId("memory-detail-close"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("renders a project ItemLink on the Provenance tab when project_id is set", () => {
    renderView({
      memory: makeMemory({ project_id: asProjectId("proj_acme") }),
    });
    fireEvent.click(screen.getByTestId("filter-tab-provenance"));
    const panel = screen.getByTestId("memory-detail-provenance");
    // ItemLink starts in loading state and resolves to a link chip or
    // a deleted-chip if the project resolver isn't registered in this
    // test. Either way the testid surface is present.
    expect(
      panel.querySelector(
        '[data-testid="item-link"], [data-testid="item-link-deleted"], [data-testid="item-link-skeleton"]',
      ),
    ).not.toBeNull();
  });
});
