// PR-1.7 — WorkspacePane composition test, run from chat-surface with a
// plain fake `WorkspacePaneState` (the stateful hook stays host-owned, so this
// unit test drives the pane directly). Covers hide-empty tabs, close routing,
// overlay attr, tabpanel wiring, and the "N live" running-agents badge.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SubagentEntry } from "@0x-copilot/api-types";

import { WorkspacePane } from "./WorkspacePane";
import type { SubagentSnapshotMap } from "./workspaceHelpers";
import type { WorkspacePaneState, WorkspacePaneTabId } from "./types";

function fakeState(
  overrides: Partial<WorkspacePaneState> = {},
): WorkspacePaneState {
  return {
    open: true,
    activeTab: "sources",
    focus: {},
    openOn: vi.fn(),
    close: vi.fn(),
    toggle: vi.fn(),
    setActiveTab: vi.fn(),
    isAutoOpenSuppressed: () => false,
    ...overrides,
  };
}

function subagent(overrides: Partial<SubagentEntry> = {}): SubagentEntry {
  return {
    task_id: "task_a",
    parent_run_id: "run_1",
    subagent_name: "doc_reader",
    status: "running",
    display_title: "Doc reader",
    objective_summary: null,
    started_at: "2026-05-06T10:00:00Z",
    completed_at: null,
    duration_ms: null,
    result_summary: null,
    safe_error_code: null,
    safe_error_message: null,
    token_usage: null,
    ...overrides,
  };
}

function subagentMap(entries: readonly SubagentEntry[]): SubagentSnapshotMap {
  return new Map(entries.map((e) => [e.task_id, e]));
}

const emptyProps = {
  sources: new Map(),
  subagents: new Map(),
  draft: null,
  approvalsQueue: { pending: [], recent: [] },
  skills: [],
} as const;

describe("WorkspacePane", () => {
  it("renders nothing when closed", () => {
    const { container } = render(
      <WorkspacePane state={fakeState({ open: false })} {...emptyProps} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders Sources + Agents tabs by default; empty tabs hide", () => {
    render(<WorkspacePane state={fakeState()} {...emptyProps} />);
    expect(screen.getAllByRole("tab")).toHaveLength(2);
    expect(screen.getByText(/Sources will appear here/)).toBeInTheDocument();
  });

  it("shows the active tab body inside a labelled tabpanel", () => {
    render(<WorkspacePane state={fakeState()} {...emptyProps} />);
    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveAttribute("aria-label", "Sources");
  });

  it("close button routes through state.close('manual')", () => {
    const close = vi.fn();
    render(<WorkspacePane state={fakeState({ close })} {...emptyProps} />);
    fireEvent.click(screen.getByTestId("workspace-pane-close"));
    expect(close).toHaveBeenCalledWith("manual");
  });

  it("data-overlay carries the responsive state", () => {
    render(<WorkspacePane state={fakeState()} {...emptyProps} overlay />);
    expect(screen.getByTestId("workspace-pane")).toHaveAttribute(
      "data-overlay",
      "true",
    );
  });

  it("agents badge shows 'N live' when subagents are running", () => {
    render(
      <WorkspacePane
        state={fakeState({ activeTab: "agents" })}
        {...emptyProps}
        subagents={subagentMap([
          subagent({ task_id: "task_a", status: "running" }),
          subagent({ task_id: "task_b", status: "completed" }),
        ])}
      />,
    );
    const agentsTab = screen.getByRole("tab", { name: /Agent/ });
    expect(agentsTab).toHaveTextContent("1 live");
  });

  it("agents badge falls back to the total when nothing is running", () => {
    render(
      <WorkspacePane
        state={fakeState({ activeTab: "agents" })}
        {...emptyProps}
        subagents={subagentMap([
          subagent({ task_id: "task_a", status: "completed" }),
          subagent({ task_id: "task_b", status: "completed" }),
        ])}
      />,
    );
    const agentsTab = screen.getByRole("tab", { name: /Agent/ });
    expect(agentsTab).toHaveTextContent("2");
    expect(agentsTab).not.toHaveTextContent("live");
  });

  it("hides empty content tabs but shows a pending approval tab", () => {
    const activeTab: WorkspacePaneTabId = "approvals";
    render(
      <WorkspacePane
        state={fakeState({ activeTab })}
        {...emptyProps}
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
      />,
    );
    // Single pending approval → singular label + count badge "1".
    const approvalsTab = screen.getByRole("tab", { name: /Approval/ });
    expect(approvalsTab).toHaveTextContent(/^Approval/);
    expect(approvalsTab).toHaveTextContent("1");
    expect(screen.getByText("Send")).toBeInTheDocument();
  });
});
