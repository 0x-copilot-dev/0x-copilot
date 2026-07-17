import type {
  ApprovalId,
  ConversationId,
  RunId,
  TenantId,
  UserId,
} from "@0x-copilot/api-types";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../providers/RouterProvider";
import {
  __resetItemRefRegistryForTests,
  registerItemRefResolver,
} from "../refs/registry";
import type { ArtifactRoute, Router } from "../routing/router";
// TODO(merge): rewire to "@0x-copilot/api-types" AssignedApproval
import type { Approval } from "../thread-canvas/_approvals-stub";

import { ApprovalsTabContent } from "./ApprovalsTabContent";

afterEach(() => {
  __resetItemRefRegistryForTests();
});

const NOW = Date.parse("2026-05-17T12:00:00.000Z");

const noopRouter: Router<ArtifactRoute> = {
  current: () => ({ kind: "chat", conversationId: "x" }) as ArtifactRoute,
  navigate: () => undefined,
  subscribe: () => () => undefined,
};

function makeApproval(overrides: Partial<Approval> = {}): Approval {
  return {
    id: (overrides.id ?? "appr_001") as ApprovalId,
    run_id: (overrides.run_id ?? "run_001") as RunId,
    conversation_id: (overrides.conversation_id ??
      "conv_001") as ConversationId,
    tenant_id: (overrides.tenant_id ?? "tnt_001") as TenantId,
    requester: (overrides.requester ?? "subagent-7") as UserId,
    target_user_id: overrides.target_user_id ?? null,
    kind: overrides.kind ?? "surface_diff",
    payload: overrides.payload ?? {},
    diff: overrides.diff,
    state: overrides.state ?? "pending",
    created_at:
      overrides.created_at ?? new Date(NOW - 5 * 60_000).toISOString(),
    resolved_at: overrides.resolved_at,
    resolution: overrides.resolution,
    context: overrides.context,
  };
}

function renderApprovals(approvals: ReadonlyArray<Approval>): void {
  registerItemRefResolver("approval", async (id) => ({
    label: `Approval ${id}`,
    icon: null,
    route: { kind: "chat", conversationId: "x" } as ArtifactRoute,
  }));
  render(
    <RouterProvider router={noopRouter}>
      <ApprovalsTabContent approvals={approvals} now={NOW} />
    </RouterProvider>,
  );
}

describe("<ApprovalsTabContent>", () => {
  it("renders a FilterTabs ARIA tablist with All / Pending / Resolved chips", () => {
    renderApprovals([]);
    const tablist = screen.getByRole("tablist", { name: "Approvals filter" });
    expect(tablist).toBeInTheDocument();
    expect(within(tablist).getByTestId("filter-tab-all")).toBeInTheDocument();
    expect(
      within(tablist).getByTestId("filter-tab-pending"),
    ).toBeInTheDocument();
    expect(
      within(tablist).getByTestId("filter-tab-resolved"),
    ).toBeInTheDocument();
  });

  it("defaults to the Pending filter and shows its empty state", () => {
    renderApprovals([]);
    expect(screen.getByTestId("approvals-tab-panel")).toHaveAttribute(
      "data-active-filter",
      "pending",
    );
    expect(screen.getByTestId("approvals-tab-empty")).toBeInTheDocument();
    expect(screen.getByTestId("empty-state-title")).toHaveTextContent(
      "No pending approvals",
    );
  });

  it("switches to All filter and renders both pending + resolved approvals", () => {
    renderApprovals([
      makeApproval({ id: "appr_a" as ApprovalId, state: "pending" }),
      makeApproval({ id: "appr_b" as ApprovalId, state: "accepted" }),
    ]);
    fireEvent.click(screen.getByTestId("filter-tab-all"));
    expect(screen.getByTestId("approvals-tab-panel")).toHaveAttribute(
      "data-active-filter",
      "all",
    );
    expect(screen.getByTestId("approvals-tab-row-appr_a")).toBeInTheDocument();
    expect(screen.getByTestId("approvals-tab-row-appr_b")).toBeInTheDocument();
  });

  it("switches to Resolved and hides pending rows", () => {
    renderApprovals([
      makeApproval({ id: "appr_a" as ApprovalId, state: "pending" }),
      makeApproval({ id: "appr_b" as ApprovalId, state: "accepted" }),
      makeApproval({ id: "appr_c" as ApprovalId, state: "rejected" }),
    ]);
    fireEvent.click(screen.getByTestId("filter-tab-resolved"));
    expect(
      screen.queryByTestId("approvals-tab-row-appr_a"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("approvals-tab-row-appr_b")).toBeInTheDocument();
    expect(screen.getByTestId("approvals-tab-row-appr_c")).toBeInTheDocument();
  });

  it("shows the per-filter empty state when no approvals match", () => {
    renderApprovals([
      makeApproval({ id: "appr_a" as ApprovalId, state: "pending" }),
    ]);
    fireEvent.click(screen.getByTestId("filter-tab-resolved"));
    expect(screen.getByTestId("approvals-tab-empty")).toHaveAttribute(
      "data-filter",
      "resolved",
    );
    expect(screen.getByTestId("empty-state-title")).toHaveTextContent(
      "No resolved approvals",
    );
  });

  it("renders an ItemLink chip, StatusPill, kind, requester, and relative timestamp on each row", async () => {
    renderApprovals([
      makeApproval({
        id: "appr_a" as ApprovalId,
        state: "pending",
        kind: "mcp_auth",
        requester: "subagent-3" as UserId,
      }),
    ]);
    const row = screen.getByTestId("approvals-tab-row-appr_a");
    expect(row).toHaveAttribute("data-state", "pending");
    // ItemLink resolves async → wait for it to appear.
    await waitFor(() =>
      expect(within(row).getByTestId("item-link")).toBeInTheDocument(),
    );
    // StatusPill present with the right tone.
    const pill = within(row).getByTestId("status-pill");
    expect(pill).toHaveAttribute("data-status", "warning");
    expect(pill).toHaveTextContent("Pending");
    // Action label = approval.kind.
    expect(
      within(row).getByTestId("approvals-tab-row-action-appr_a"),
    ).toHaveTextContent("mcp_auth");
    expect(
      within(row).getByTestId("approvals-tab-row-requester-appr_a"),
    ).toHaveTextContent("subagent-3");
    const time = within(row).getByTestId("approvals-tab-row-time-appr_a");
    expect(time.tagName).toBe("TIME");
  });

  it("maps states to status-pill tones (pending=warning, accepted=ok, rejected=error, edited=info)", async () => {
    renderApprovals([
      makeApproval({ id: "appr_p" as ApprovalId, state: "pending" }),
      makeApproval({ id: "appr_a" as ApprovalId, state: "accepted" }),
      makeApproval({ id: "appr_r" as ApprovalId, state: "rejected" }),
      makeApproval({ id: "appr_e" as ApprovalId, state: "edited" }),
    ]);
    fireEvent.click(screen.getByTestId("filter-tab-all"));
    const toneFor = (id: string): string | null =>
      within(screen.getByTestId(`approvals-tab-row-${id}`))
        .getByTestId("status-pill")
        .getAttribute("data-status");
    expect(toneFor("appr_p")).toBe("warning");
    expect(toneFor("appr_a")).toBe("ok");
    expect(toneFor("appr_r")).toBe("error");
    expect(toneFor("appr_e")).toBe("info");
  });

  it("calls onFilterChange when controlled by the host", () => {
    const onFilterChange = vi.fn();
    registerItemRefResolver("approval", async (id) => ({
      label: `Approval ${id}`,
      icon: null,
      route: { kind: "chat", conversationId: "x" } as ArtifactRoute,
    }));
    render(
      <RouterProvider router={noopRouter}>
        <ApprovalsTabContent
          approvals={[]}
          filter="all"
          onFilterChange={onFilterChange}
          now={NOW}
        />
      </RouterProvider>,
    );
    expect(screen.getByTestId("approvals-tab-panel")).toHaveAttribute(
      "data-active-filter",
      "all",
    );
    fireEvent.click(screen.getByTestId("filter-tab-pending"));
    expect(onFilterChange).toHaveBeenCalledWith("pending");
    // controlled — internal state cannot override the prop.
    expect(screen.getByTestId("approvals-tab-panel")).toHaveAttribute(
      "data-active-filter",
      "all",
    );
  });

  it("shows live counts on each filter chip", () => {
    renderApprovals([
      makeApproval({ id: "appr_a" as ApprovalId, state: "pending" }),
      makeApproval({ id: "appr_b" as ApprovalId, state: "pending" }),
      makeApproval({ id: "appr_c" as ApprovalId, state: "accepted" }),
    ]);
    expect(screen.getByTestId("filter-tab-count-all")).toHaveTextContent("3");
    expect(screen.getByTestId("filter-tab-count-pending")).toHaveTextContent(
      "2",
    );
    expect(screen.getByTestId("filter-tab-count-resolved")).toHaveTextContent(
      "1",
    );
  });
});
