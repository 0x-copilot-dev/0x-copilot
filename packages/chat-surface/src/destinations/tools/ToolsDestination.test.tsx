// ToolsDestination + ToolCard + ToolsPanel — unit tests (P10-B1).
//
// Covers the catalog shell, filter axis, kind narrow, search debounce
// callback, empty / onboarding tile path, sort selector, card chip
// structure, status-tone mapping, panel filters, and ARIA wiring per
// the scope brief.

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  Tool,
  ToolId,
  ToolStatus,
  TenantId,
  UserId,
} from "@enterprise-search/api-types";

import { ToolCard } from "./ToolCard";
import { ToolsDestination } from "./ToolsDestination";
import { ToolsPanel } from "./ToolsPanel";
import { statusTone, type ToolKind, type ToolScope } from "./_tools-stub";

// ===========================================================================
// Fixtures
// ===========================================================================

interface MakeToolOverrides {
  readonly id?: string;
  readonly name?: string;
  readonly description?: string;
  readonly kind?: ToolKind;
  readonly scope?: ToolScope;
  readonly status?: ToolStatus;
  readonly owner_user_id?: string;
  readonly calls_30d?: number;
  readonly last_used_at?: string | null;
  readonly tags?: ReadonlyArray<string>;
  readonly created_at?: string;
}

function makeTool(overrides: MakeToolOverrides = {}): Tool {
  // Use the `in` operator (not `??`) so callers can explicitly pass
  // `last_used_at: null` and override the default ISO timestamp.
  const lastUsed: string | null =
    "last_used_at" in overrides
      ? (overrides.last_used_at ?? null)
      : "2026-05-15T00:00:00Z";
  return {
    id: (overrides.id ?? "tool_1") as ToolId,
    tenant_id: "tnt_1" as TenantId,
    name: overrides.name ?? "Search Web",
    description: overrides.description ?? "Search the web with Bing.",
    kind: overrides.kind ?? "builtin",
    scope: overrides.scope ?? "read",
    status: overrides.status ?? "enabled",
    args_schema: { type: "object" },
    returns_schema: { type: "object" },
    transport: { kind: "in_process", executor: "web_search" },
    owner_user_id: (overrides.owner_user_id ?? "user_owner") as UserId,
    tags: overrides.tags ?? [],
    usage: {
      calls_24h: 0,
      calls_30d: overrides.calls_30d ?? 12,
      p50_latency_ms_30d: 120,
      success_rate_30d: 0.98,
      last_used_at: lastUsed,
    },
    created_at: overrides.created_at ?? "2026-04-01T00:00:00Z",
    updated_at: "2026-05-15T00:00:00Z",
  };
}

const SAMPLE: ReadonlyArray<Tool> = [
  makeTool({
    id: "tool_a",
    name: "Search Web",
    kind: "builtin",
    scope: "read",
    status: "enabled",
    owner_user_id: "user_me",
    calls_30d: 80,
    last_used_at: "2026-05-17T08:00:00Z",
  }),
  makeTool({
    id: "tool_b",
    name: "Send Slack message",
    kind: "mcp",
    scope: "write",
    status: "enabled",
    owner_user_id: "user_other",
    calls_30d: 20,
    last_used_at: "2026-05-10T08:00:00Z",
  }),
  makeTool({
    id: "tool_c",
    name: "Pending OpenAPI",
    kind: "openapi",
    scope: "both",
    status: "pending_review",
    owner_user_id: "user_me",
    calls_30d: 0,
    last_used_at: null,
  }),
  makeTool({
    id: "tool_d",
    name: "My Custom Routine",
    kind: "code",
    scope: "read",
    status: "disabled",
    owner_user_id: "user_me",
    calls_30d: 4,
    last_used_at: "2026-04-01T00:00:00Z",
  }),
  makeTool({
    id: "tool_e",
    name: "Summarize skill",
    kind: "skill",
    scope: "read",
    status: "error",
    owner_user_id: "user_other",
    calls_30d: 1,
    last_used_at: "2026-05-01T00:00:00Z",
  }),
];

// ===========================================================================
// ToolCard
// ===========================================================================

describe("ToolCard", () => {
  it("renders the name, kind chip, scope chip, status pill, and 30d calls", () => {
    render(<ToolCard tool={makeTool({ name: "Search Web", calls_30d: 42 })} />);
    expect(screen.getByTestId("tool-card-name")).toHaveTextContent(
      "Search Web",
    );
    expect(screen.getByTestId("tool-card-kind")).toHaveAttribute(
      "data-tool-kind",
      "builtin",
    );
    expect(screen.getByTestId("tool-card-scope")).toHaveAttribute(
      "data-tool-scope",
      "read",
    );
    expect(screen.getByTestId("tool-card-calls")).toHaveTextContent("42 calls");
    expect(screen.getByTestId("status-pill")).toHaveAttribute(
      "data-status",
      "ok",
    );
  });

  it("maps each ToolStatus to the correct StatusPill tone", () => {
    expect(statusTone("enabled")).toBe("ok");
    expect(statusTone("error")).toBe("error");
    expect(statusTone("pending_review")).toBe("warning");
    expect(statusTone("disabled")).toBe("muted");
  });

  it("renders the status pill with the warning tone for pending_review", () => {
    render(<ToolCard tool={makeTool({ status: "pending_review" })} />);
    expect(screen.getByTestId("status-pill")).toHaveAttribute(
      "data-status",
      "warning",
    );
  });

  it("fires onOpen when the card is clicked", () => {
    const onOpen = vi.fn();
    const tool = makeTool({ id: "tool_open" });
    render(<ToolCard tool={tool} onOpen={onOpen} />);
    fireEvent.click(screen.getByTestId("tool-card"));
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onOpen).toHaveBeenCalledWith(tool);
  });

  it("renders 'never' for last-used when usage.last_used_at is null", () => {
    render(<ToolCard tool={makeTool({ last_used_at: null })} />);
    expect(screen.getByTestId("tool-card-last-used")).toHaveTextContent(
      "never",
    );
  });
});

// ===========================================================================
// ToolsDestination — render + ARIA
// ===========================================================================

describe("ToolsDestination", () => {
  it("renders the PageHeader, filter tabs, and search bar", () => {
    render(<ToolsDestination tools={SAMPLE} currentUserId="user_me" />);
    expect(screen.getByTestId("page-header-title")).toHaveTextContent("Tools");
    expect(screen.getByTestId("filter-tabs")).toBeInTheDocument();
    expect(screen.getByTestId("tools-search")).toBeInTheDocument();
    expect(screen.getByTestId("tools-sort")).toBeInTheDocument();
  });

  it("wraps the filter tablist in <nav aria-label='Tools filter'>", () => {
    render(<ToolsDestination tools={SAMPLE} currentUserId="user_me" />);
    const nav = screen.getByRole("navigation", { name: "Tools filter" });
    expect(nav).toBeInTheDocument();
    expect(within(nav).getByTestId("filter-tabs")).toBeInTheDocument();
  });

  it("renders the grid as <region aria-label='Tools catalog'> when not empty", () => {
    render(<ToolsDestination tools={SAMPLE} currentUserId="user_me" />);
    // Default "My" filter (currentUserId=user_me) + status=enabled → 1 tool
    const region = screen.getByRole("region", { name: "Tools catalog" });
    expect(region).toBeInTheDocument();
    expect(within(region).getAllByTestId("tool-card")).toHaveLength(1);
  });

  // ---- Filter axis narrowing -------------------------------------------

  it("My (with currentUserId) shows owned + enabled tools only", () => {
    render(<ToolsDestination tools={SAMPLE} currentUserId="user_me" />);
    const cards = screen.getAllByTestId("tool-card");
    // user_me owns tool_a (enabled), tool_c (pending_review), tool_d (disabled)
    // Only tool_a is enabled → 1 card.
    expect(cards).toHaveLength(1);
    expect(cards[0]).toHaveAttribute("data-tool-id", "tool_a");
  });

  it("Installed shows every tool with status=enabled", () => {
    render(<ToolsDestination tools={SAMPLE} currentUserId="user_me" />);
    fireEvent.click(screen.getByTestId("filter-tab-installed"));
    const cards = screen.getAllByTestId("tool-card");
    expect(cards.map((c) => c.getAttribute("data-tool-id")).sort()).toEqual([
      "tool_a",
      "tool_b",
    ]);
  });

  it("Available shows every tool whose status is NOT enabled", () => {
    render(<ToolsDestination tools={SAMPLE} currentUserId="user_me" />);
    fireEvent.click(screen.getByTestId("filter-tab-available"));
    const cards = screen.getAllByTestId("tool-card");
    expect(cards.map((c) => c.getAttribute("data-tool-id")).sort()).toEqual([
      "tool_c",
      "tool_d",
      "tool_e",
    ]);
  });

  it("Custom narrows to kind=code or kind=skill", () => {
    render(<ToolsDestination tools={SAMPLE} currentUserId="user_me" />);
    fireEvent.click(screen.getByTestId("filter-tab-custom"));
    const cards = screen.getAllByTestId("tool-card");
    expect(cards.map((c) => c.getAttribute("data-tool-id")).sort()).toEqual([
      "tool_d",
      "tool_e",
    ]);
  });

  // ---- By kind pill row ------------------------------------------------

  it("By kind reveals the kind-pill row and narrows the catalog", () => {
    render(<ToolsDestination tools={SAMPLE} currentUserId="user_me" />);
    fireEvent.click(screen.getByTestId("filter-tab-by_kind"));
    expect(screen.getByTestId("tools-kind-row")).toBeInTheDocument();
    expect(screen.getByTestId("tools-kind-chip-mcp")).toBeInTheDocument();
    expect(screen.getByTestId("tools-kind-chip-openapi")).toBeInTheDocument();
    expect(screen.getByTestId("tools-kind-chip-builtin")).toBeInTheDocument();
    expect(screen.getByTestId("tools-kind-chip-code")).toBeInTheDocument();
    expect(screen.getByTestId("tools-kind-chip-skill")).toBeInTheDocument();
    // All 5 sample tools when no kind chip is pressed.
    expect(screen.getAllByTestId("tool-card")).toHaveLength(5);

    fireEvent.click(screen.getByTestId("tools-kind-chip-mcp"));
    const cards = screen.getAllByTestId("tool-card");
    expect(cards).toHaveLength(1);
    expect(cards[0]).toHaveAttribute("data-tool-kind", "mcp");
  });

  it("hides the kind-pill row when the filter is not by_kind", () => {
    render(<ToolsDestination tools={SAMPLE} currentUserId="user_me" />);
    expect(screen.queryByTestId("tools-kind-row")).toBeNull();
  });

  // ---- Search ----------------------------------------------------------

  it("search input invokes onSearchChange with each keystroke", () => {
    const onSearchChange = vi.fn();
    render(
      <ToolsDestination
        tools={SAMPLE}
        currentUserId="user_me"
        onSearchChange={onSearchChange}
      />,
    );
    fireEvent.change(screen.getByTestId("tools-search"), {
      target: { value: "slack" },
    });
    expect(onSearchChange).toHaveBeenCalledWith("slack");
  });

  it("search narrows results case-insensitively over name + description + tags", () => {
    render(<ToolsDestination tools={SAMPLE} currentUserId="user_me" />);
    fireEvent.click(screen.getByTestId("filter-tab-installed"));
    fireEvent.change(screen.getByTestId("tools-search"), {
      target: { value: "SLACK" },
    });
    const cards = screen.getAllByTestId("tool-card");
    expect(cards).toHaveLength(1);
    expect(cards[0]).toHaveAttribute("data-tool-id", "tool_b");
  });

  // ---- Sort ------------------------------------------------------------

  it("changing sort updates the visible order (calls_30d desc)", () => {
    render(<ToolsDestination tools={SAMPLE} currentUserId="user_me" />);
    fireEvent.click(screen.getByTestId("filter-tab-installed"));
    fireEvent.change(screen.getByTestId("tools-sort"), {
      target: { value: "calls_30d_desc" },
    });
    const cards = screen.getAllByTestId("tool-card");
    // tool_a (80 calls) should precede tool_b (20 calls).
    expect(cards[0]).toHaveAttribute("data-tool-id", "tool_a");
    expect(cards[1]).toHaveAttribute("data-tool-id", "tool_b");
  });

  it("the sort select exposes exactly the four allowlisted sorts", () => {
    render(<ToolsDestination tools={SAMPLE} currentUserId="user_me" />);
    const select = screen.getByTestId("tools-sort") as HTMLSelectElement;
    const values = Array.from(select.options).map((o) => o.value);
    expect(values).toEqual([
      "name_asc",
      "calls_30d_desc",
      "last_used_desc",
      "created_at_desc",
    ]);
  });

  // ---- Empty + onboarding tiles ----------------------------------------

  it("renders the 4-tile EmptyState when the tools list is empty", () => {
    render(<ToolsDestination tools={[]} />);
    expect(screen.getByTestId("empty-state")).toBeInTheDocument();
    expect(screen.getByTestId("tools-onboard-tiles")).toBeInTheDocument();
    expect(screen.getByTestId("tools-onboard-tile-mcp")).toBeInTheDocument();
    expect(
      screen.getByTestId("tools-onboard-tile-openapi"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("tools-onboard-tile-code")).toBeInTheDocument();
    expect(screen.getByTestId("tools-onboard-tile-skill")).toBeInTheDocument();
  });

  it("clicking an onboarding tile fires onOnboard(kind)", () => {
    const onOnboard = vi.fn();
    render(<ToolsDestination tools={[]} onOnboard={onOnboard} />);
    fireEvent.click(screen.getByTestId("tools-onboard-tile-code"));
    expect(onOnboard).toHaveBeenCalledWith("code");
  });

  it("renders the 'No tools match' empty state when tools are present but filter narrows to zero", () => {
    render(
      <ToolsDestination
        tools={[
          makeTool({ id: "tool_xx", kind: "builtin", status: "enabled" }),
        ]}
        currentUserId="user_other"
      />,
    );
    // Default filter is "my" + user_other doesn't own tool_xx → 0 visible.
    fireEvent.click(screen.getByTestId("filter-tab-custom"));
    expect(screen.getByText("No tools match")).toBeInTheDocument();
    expect(screen.queryByTestId("tools-onboard-tiles")).toBeNull();
  });

  // ---- Open callback ---------------------------------------------------

  it("clicking a tool card fires onOpenTool", () => {
    const onOpenTool = vi.fn();
    render(
      <ToolsDestination
        tools={SAMPLE}
        currentUserId="user_me"
        onOpenTool={onOpenTool}
      />,
    );
    fireEvent.click(screen.getAllByTestId("tool-card")[0]);
    expect(onOpenTool).toHaveBeenCalledTimes(1);
  });

  // ---- Primary action --------------------------------------------------

  it("clicking the PageHeader Onboard primary action fires onOnboard()", () => {
    const onOnboard = vi.fn();
    render(<ToolsDestination tools={SAMPLE} onOnboard={onOnboard} />);
    fireEvent.click(screen.getByTestId("page-header-primary-action"));
    expect(onOnboard).toHaveBeenCalledTimes(1);
    expect(onOnboard).toHaveBeenCalledWith(); // no kind for the header CTA
  });
});

// ===========================================================================
// ToolsPanel
// ===========================================================================

describe("ToolsPanel", () => {
  it("renders the kind / scope / status sections with All chips", () => {
    render(<ToolsPanel />);
    expect(screen.getByTestId("tools-panel")).toBeInTheDocument();
    expect(screen.getByTestId("tools-panel-section-kind")).toBeInTheDocument();
    expect(screen.getByTestId("tools-panel-section-scope")).toBeInTheDocument();
    expect(
      screen.getByTestId("tools-panel-section-status"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("tools-panel-kind-all")).toBeInTheDocument();
    expect(screen.getByTestId("tools-panel-scope-all")).toBeInTheDocument();
    expect(screen.getByTestId("tools-panel-status-all")).toBeInTheDocument();
  });

  it("each kind has a chip; clicking fires onKindFilterChange", () => {
    const onKindFilterChange = vi.fn();
    render(<ToolsPanel onKindFilterChange={onKindFilterChange} />);
    fireEvent.click(screen.getByTestId("tools-panel-kind-mcp"));
    expect(onKindFilterChange).toHaveBeenCalledWith("mcp");
  });

  it("clicking an active kind chip clears the filter (toggle)", () => {
    const onKindFilterChange = vi.fn();
    render(
      <ToolsPanel kindFilter="mcp" onKindFilterChange={onKindFilterChange} />,
    );
    fireEvent.click(screen.getByTestId("tools-panel-kind-mcp"));
    expect(onKindFilterChange).toHaveBeenCalledWith(null);
  });

  it("scope chips fire onScopeFilterChange", () => {
    const onScopeFilterChange = vi.fn();
    render(<ToolsPanel onScopeFilterChange={onScopeFilterChange} />);
    fireEvent.click(screen.getByTestId("tools-panel-scope-write"));
    expect(onScopeFilterChange).toHaveBeenCalledWith("write");
  });

  it("status chips fire onStatusFilterChange", () => {
    const onStatusFilterChange = vi.fn();
    render(<ToolsPanel onStatusFilterChange={onStatusFilterChange} />);
    fireEvent.click(screen.getByTestId("tools-panel-status-error"));
    expect(onStatusFilterChange).toHaveBeenCalledWith("error");
  });

  it("onboard CTA renders only when callback provided", () => {
    const { rerender } = render(<ToolsPanel />);
    expect(screen.queryByText("Onboard tool")).toBeNull();
    rerender(<ToolsPanel onOnboard={() => undefined} />);
    expect(screen.getByText("Onboard tool")).toBeInTheDocument();
  });
});
