// PR 3.2 — WorkspacePane composition test. Verifies open/close
// behavior, tab switching, and that only the active tab body renders.

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

  it("renders five tabs when open and shows the active body", () => {
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
    expect(screen.getAllByRole("tab")).toHaveLength(5);
    expect(screen.getByText(/Sources will appear here/)).toBeInTheDocument();
  });

  it("switching tabs swaps the body and only the body", () => {
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
        approvalsQueue={{ pending: [], recent: [] }}
        skills={[]}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: /Approvals/ }));
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
    expect(screen.getByText(/No pending approvals/)).toBeInTheDocument();
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
        approvalsQueue={{
          pending: [
            {
              approvalId: "ap-1",
              title: "Send",
              summary: null,
              approvalKind: "tool_action",
              runId: null,
              messageId: "m1",
              resolved: false,
              resolvedAt: null,
              target: null,
            },
          ],
          recent: [],
        }}
        skills={[]}
      />,
    );
    // Approvals tab should carry a "1" badge.
    const approvalsTab = screen.getByRole("tab", { name: /Approvals/ });
    expect(approvalsTab).toHaveTextContent("1");
  });
});
