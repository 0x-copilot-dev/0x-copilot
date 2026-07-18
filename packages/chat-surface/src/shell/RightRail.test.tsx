import type {
  ApprovalId,
  ConversationId,
  RunId,
  TenantId,
  UserId,
} from "@0x-copilot/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../providers/RouterProvider";
import {
  __resetItemRefRegistryForTests,
  registerItemRefResolver,
} from "../refs/registry";
import type { ArtifactRoute, Router } from "../routing/router";
// TODO(merge): rewire to "@0x-copilot/api-types" AssignedApproval
import type { Approval } from "../thread-canvas/_approvals-stub";
import type { ActivityEntry } from "../thread-canvas/eventProjector";

import { RightRail } from "./RightRail";

afterEach(() => {
  __resetItemRefRegistryForTests();
});

const NOW = Date.parse("2026-05-17T12:00:00.000Z");

const noopRouter: Router<ArtifactRoute> = {
  current: () => ({ kind: "chat", conversationId: "x" }) as ArtifactRoute,
  navigate: () => undefined,
  subscribe: () => () => undefined,
};

function makeActivity(overrides: Partial<ActivityEntry> = {}): ActivityEntry {
  return {
    id: overrides.id ?? "act-1",
    sequenceNo: overrides.sequenceNo ?? 1,
    kind: overrides.kind ?? "tool",
    title: overrides.title ?? "Fetch sheet",
    summary: overrides.summary,
    status: overrides.status,
    createdAt: overrides.createdAt ?? new Date(NOW - 60_000).toISOString(),
    subagentId: overrides.subagentId,
    surfaceUri: overrides.surfaceUri,
  };
}

function makeApproval(overrides: Partial<Approval> = {}): Approval {
  return {
    id: (overrides.id ?? "appr_001") as ApprovalId,
    run_id: (overrides.run_id ?? "run_001") as RunId,
    conversation_id: (overrides.conversation_id ??
      "conv_001") as ConversationId,
    tenant_id: (overrides.tenant_id ?? "tnt_001") as TenantId,
    requester: (overrides.requester ?? "sub-1") as UserId,
    target_user_id: overrides.target_user_id ?? null,
    kind: overrides.kind ?? "surface_diff",
    payload: overrides.payload ?? {},
    diff: overrides.diff,
    state: overrides.state ?? "pending",
    created_at: overrides.created_at ?? new Date(NOW - 120_000).toISOString(),
    resolved_at: overrides.resolved_at,
    resolution: overrides.resolution,
    context: overrides.context,
  };
}

describe("RightRail (chrome only)", () => {
  it("renders the Copilot conversation header and a neutral empty state when open with no children or tab data", () => {
    render(<RightRail open={true} onToggle={() => {}} />);
    expect(
      screen.getByRole("complementary", { name: "Copilot conversation" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Placeholder message/i)).not.toBeInTheDocument();
    expect(screen.getByTestId("right-rail-empty")).toBeInTheDocument();
  });

  it("renders host-supplied children inside the body (children win over tab data)", () => {
    render(
      <RightRail open={true} onToggle={() => {}} activity={[]} approvals={[]}>
        <div data-testid="rail-child">live thread</div>
      </RightRail>,
    );
    expect(screen.getByTestId("rail-child")).toBeInTheDocument();
    expect(screen.queryByTestId("right-rail-empty")).not.toBeInTheDocument();
    expect(screen.queryByTestId("right-rail-tabpanel")).not.toBeInTheDocument();
  });

  it("hides the body when closed", () => {
    render(<RightRail open={false} onToggle={() => {}} />);
    expect(screen.queryByTestId("right-rail-body")).not.toBeInTheDocument();
    expect(screen.queryByTestId("right-rail-empty")).not.toBeInTheDocument();
  });

  it("renders the toggle button in both states", () => {
    const { rerender } = render(<RightRail open={true} onToggle={() => {}} />);
    expect(screen.getByTestId("right-rail-toggle")).toBeInTheDocument();
    expect(screen.getByTestId("right-rail-toggle")).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    rerender(<RightRail open={false} onToggle={() => {}} />);
    expect(screen.getByTestId("right-rail-toggle")).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("calls onToggle when the toggle button is clicked", () => {
    const onToggle = vi.fn();
    render(<RightRail open={true} onToggle={onToggle} />);
    fireEvent.click(screen.getByTestId("right-rail-toggle"));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("re-labels the rail when the host passes a title", () => {
    render(
      <RightRail open={true} onToggle={() => {}} title="Approvals queue" />,
    );
    expect(
      screen.getByRole("complementary", { name: "Approvals queue" }),
    ).toBeInTheDocument();
  });
});

describe("RightRail (tabs view)", () => {
  it("renders Activity + Approvals tabs when both data arrays are supplied", () => {
    render(
      <RouterProvider router={noopRouter}>
        <RightRail
          open={true}
          onToggle={() => {}}
          activity={[makeActivity()]}
          approvals={[]}
          now={NOW}
        />
      </RouterProvider>,
    );
    expect(
      screen.getByRole("tablist", { name: "Thread context" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("filter-tab-activity")).toBeInTheDocument();
    expect(screen.getByTestId("filter-tab-approvals")).toBeInTheDocument();
    expect(screen.getByTestId("right-rail-tabpanel")).toHaveAttribute(
      "data-active-tab",
      "activity",
    );
    expect(screen.queryByTestId("right-rail-empty")).not.toBeInTheDocument();
  });

  it("switches to Approvals when its tab is clicked (uncontrolled)", () => {
    registerItemRefResolver("approval", async (id) => ({
      label: `Approval ${id}`,
      icon: null,
      route: { kind: "chat", conversationId: "x" } as ArtifactRoute,
    }));
    render(
      <RouterProvider router={noopRouter}>
        <RightRail
          open={true}
          onToggle={() => {}}
          activity={[makeActivity()]}
          approvals={[makeApproval()]}
          now={NOW}
        />
      </RouterProvider>,
    );
    expect(screen.getByTestId("right-rail-tabpanel")).toHaveAttribute(
      "data-active-tab",
      "activity",
    );
    fireEvent.click(screen.getByTestId("filter-tab-approvals"));
    expect(screen.getByTestId("right-rail-tabpanel")).toHaveAttribute(
      "data-active-tab",
      "approvals",
    );
    expect(screen.getByTestId("approvals-tab-content")).toBeInTheDocument();
  });

  it("shows the pending-approval count on the Approvals tab pill", () => {
    render(
      <RouterProvider router={noopRouter}>
        <RightRail
          open={true}
          onToggle={() => {}}
          activity={[]}
          approvals={[
            makeApproval({ id: "appr_001" as ApprovalId, state: "pending" }),
            makeApproval({ id: "appr_002" as ApprovalId, state: "pending" }),
            makeApproval({ id: "appr_003" as ApprovalId, state: "accepted" }),
          ]}
          now={NOW}
        />
      </RouterProvider>,
    );
    expect(screen.getByTestId("filter-tab-count-approvals")).toHaveTextContent(
      "2",
    );
  });

  it("hides the count pill when there are zero pending approvals", () => {
    render(
      <RouterProvider router={noopRouter}>
        <RightRail
          open={true}
          onToggle={() => {}}
          activity={[]}
          approvals={[
            makeApproval({ id: "appr_001" as ApprovalId, state: "accepted" }),
          ]}
          now={NOW}
        />
      </RouterProvider>,
    );
    expect(
      screen.queryByTestId("filter-tab-count-approvals"),
    ).not.toBeInTheDocument();
  });

  it("controlled activeTab: caller drives the active tab and onTabChange fires", () => {
    const onTabChange = vi.fn();
    const { rerender } = render(
      <RouterProvider router={noopRouter}>
        <RightRail
          open={true}
          onToggle={() => {}}
          activity={[makeActivity()]}
          approvals={[makeApproval()]}
          activeTab="activity"
          onTabChange={onTabChange}
          now={NOW}
        />
      </RouterProvider>,
    );
    expect(screen.getByTestId("right-rail-tabpanel")).toHaveAttribute(
      "data-active-tab",
      "activity",
    );
    fireEvent.click(screen.getByTestId("filter-tab-approvals"));
    expect(onTabChange).toHaveBeenCalledWith("approvals");
    // controlled — internal state must NOT shift the active tab.
    expect(screen.getByTestId("right-rail-tabpanel")).toHaveAttribute(
      "data-active-tab",
      "activity",
    );
    // Caller flips the prop.
    rerender(
      <RouterProvider router={noopRouter}>
        <RightRail
          open={true}
          onToggle={() => {}}
          activity={[makeActivity()]}
          approvals={[makeApproval()]}
          activeTab="approvals"
          onTabChange={onTabChange}
          now={NOW}
        />
      </RouterProvider>,
    );
    expect(screen.getByTestId("right-rail-tabpanel")).toHaveAttribute(
      "data-active-tab",
      "approvals",
    );
  });
});
