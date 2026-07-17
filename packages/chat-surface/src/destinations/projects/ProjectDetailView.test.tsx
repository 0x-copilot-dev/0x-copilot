import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ProjectDetailView, type ProjectDetail } from "./ProjectDetailView";
import type { ProjectId } from "@0x-copilot/api-types";

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

function renderView(
  overrides: Partial<React.ComponentProps<typeof ProjectDetailView>> = {},
) {
  const renderCrossDestinationTab = vi.fn((tab: string, projectId: string) => (
    <div data-testid={`stub-${tab}`} data-project-id={projectId}>
      {tab} stub
    </div>
  ));
  return {
    renderCrossDestinationTab,
    ...render(
      <ProjectDetailView
        project={PROJECT}
        members={[]}
        activity={[]}
        canManage={false}
        renderCrossDestinationTab={renderCrossDestinationTab}
        {...overrides}
      />,
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
    expect(screen.getByTestId("project-detail-icon").textContent).toBe("🚀");
    expect(screen.getByTestId("project-detail-icon")).toHaveAttribute(
      "data-color-hue",
      "220",
    );
  });

  it("renders all seven tabs in order", () => {
    renderView();
    const tabs = screen.getByTestId("project-detail-tabs");
    const buttons = tabs.querySelectorAll('[role="tab"]');
    const ids = Array.from(buttons).map((b) => b.getAttribute("data-testid"));
    expect(ids).toEqual([
      "project-detail-tab-chats",
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
    renderView({ onTabChange });
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
    renderView({ activeTab: "library", onTabChange });
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
    renderView({ initialTab: "members" });
    expect(
      screen.getByTestId("project-detail-panel-members"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("project-members-tab")).toBeInTheDocument();
  });

  it("renders activity tab content when active", () => {
    renderView({ initialTab: "activity" });
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
});
