// PR 3.2 — WorkspacePane composition test. Verifies open/close
// behavior, tab switching, and that only the active tab body renders.
// PR 8.0.1 — empty tabs (Draft / Approvals / Skills) hide; tab labels
// pluralise per count.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderHook, act } from "@testing-library/react";

import { emptySourceMap } from "../../chatModel/sourcesReducer";
import { emptySubagentMap } from "../../chatModel/subagentReducer";
import { WorkspacePane } from "./WorkspacePane";
import { useWorkspacePaneState } from "./useWorkspacePaneState";

function buildPane(initial: Parameters<typeof useWorkspacePaneState>[0]) {
  return renderHook(() => useWorkspacePaneState(initial));
}

const SINGLE_PENDING_APPROVAL = {
  pending: [
    {
      approvalId: "ap-1",
      title: "Send",
      summary: null,
      approvalKind: "tool_action" as const,
      runId: null,
      messageId: "m1",
      resolved: false,
      resolvedAt: null,
      target: null,
    },
  ],
  recent: [],
};

describe("WorkspacePane", () => {
  it("renders nothing when closed", () => {
    const { result } = buildPane({ conversationId: "c1" });
    const { container } = render(
      <WorkspacePane
        state={result.current}
        sources={emptySourceMap()}
        subagents={emptySubagentMap()}
        draft={null}
        approvalsQueue={{ pending: [], recent: [] }}
        skills={[]}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders Sources + Agents tabs by default; empty tabs hide", () => {
    // PR 8.0.1 — Draft / Approvals / Skills are conditional on having
    // content. With nothing seeded, only Sources + Agents render.
    const { result } = buildPane({
      conversationId: "c1",
      initialOpen: true,
      initialTab: "sources",
    });
    render(
      <WorkspacePane
        state={result.current}
        sources={emptySourceMap()}
        subagents={emptySubagentMap()}
        draft={null}
        approvalsQueue={{ pending: [], recent: [] }}
        skills={[]}
      />,
    );
    expect(screen.getAllByRole("tab")).toHaveLength(2);
    expect(screen.getByText(/Sources will appear here/)).toBeInTheDocument();
  });

  it("switching tabs swaps the body and only the body", () => {
    // Seed a pending approval so the Approvals tab is visible.
    const { result } = buildPane({
      conversationId: "c1",
      initialOpen: true,
    });
    const { rerender } = render(
      <WorkspacePane
        state={result.current}
        sources={emptySourceMap()}
        subagents={emptySubagentMap()}
        draft={null}
        approvalsQueue={SINGLE_PENDING_APPROVAL}
        skills={[]}
      />,
    );
    // Single pending approval → singular "Approval" label per pluralize.
    fireEvent.click(screen.getByRole("tab", { name: /Approval/ }));
    rerender(
      <WorkspacePane
        state={result.current}
        sources={emptySourceMap()}
        subagents={emptySubagentMap()}
        draft={null}
        approvalsQueue={SINGLE_PENDING_APPROVAL}
        skills={[]}
      />,
    );
    // The pending approval row carries the seeded title.
    expect(screen.getByText("Send")).toBeInTheDocument();
  });

  it("close button closes the pane", () => {
    const { result } = buildPane({
      conversationId: "c1",
      initialOpen: true,
    });
    const { rerender, container } = render(
      <WorkspacePane
        state={result.current}
        sources={emptySourceMap()}
        subagents={emptySubagentMap()}
        draft={null}
        approvalsQueue={{ pending: [], recent: [] }}
        skills={[]}
      />,
    );
    fireEvent.click(screen.getByTestId("workspace-pane-close"));
    rerender(
      <WorkspacePane
        state={result.current}
        sources={emptySourceMap()}
        subagents={emptySubagentMap()}
        draft={null}
        approvalsQueue={{ pending: [], recent: [] }}
        skills={[]}
      />,
    );
    expect(container.firstChild).toBeNull();
    // Manual close poisons the auto-open memory.
    expect(result.current.isAutoOpenSuppressed("c1")).toBe(true);
  });

  it("data-overlay carries the responsive state", () => {
    const { result } = buildPane({
      conversationId: "c1",
      initialOpen: true,
    });
    render(
      <WorkspacePane
        state={result.current}
        sources={emptySourceMap()}
        subagents={emptySubagentMap()}
        draft={null}
        approvalsQueue={{ pending: [], recent: [] }}
        skills={[]}
        overlay
      />,
    );
    expect(screen.getByTestId("workspace-pane")).toHaveAttribute(
      "data-overlay",
      "true",
    );
  });

  it("activeTab badges reflect data sizes", () => {
    const { result } = buildPane({
      conversationId: "c1",
      initialOpen: true,
    });
    act(() => result.current.setActiveTab("approvals"));
    render(
      <WorkspacePane
        state={result.current}
        sources={emptySourceMap()}
        subagents={emptySubagentMap()}
        draft={null}
        approvalsQueue={SINGLE_PENDING_APPROVAL}
        skills={[]}
      />,
    );
    // PR 8.0.1 — single pending approval → singular label "Approval"
    // (was "Approvals" pre-pluralize) and the badge count is "1".
    const approvalsTab = screen.getByRole("tab", { name: /Approval/ });
    expect(approvalsTab).toHaveTextContent("1");
    expect(approvalsTab).toHaveTextContent(/^Approval/);
  });
});
