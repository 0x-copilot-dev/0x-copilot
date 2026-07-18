// RunWorkspaceRail — tabbed right-rail tests (PR-3.6).
//
// Covers the FRs the rail owns:
//   FR-3.10 — tab order `Chat · Sources · Agents · Approvals`, Chat default,
//             role="tablist"/tab/tabpanel, arrow-key nav.
//   FR-3.11 — Chat hosts the injected chatSlot; Sources/Agents/Approvals reuse
//             the hoisted WorkspacePane bodies; Draft + Skills absent.
//   FR-3.12 — Agents "N live" / Approvals pending badges when >0; per-tab empty
//             copy otherwise.
//   FR-3.13 — Focus mode collapses the rail to Chat-only (tab chrome gone).
//
// The rail owns no I/O — it is driven with the same chat-surface-local shapes
// WorkspacePane consumes, so no providers are needed.

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SourceEntry, SubagentEntry } from "@0x-copilot/api-types";

import type {
  ApprovalsQueueItem,
  ApprovalsQueueProjection,
  SourceEntryMap,
  SubagentSnapshotMap,
} from "../../workspace";
import { RunWorkspaceRail } from "./RunWorkspaceRail";

// ============================================================
// Fixtures
// ============================================================

function chatSlot() {
  return <div data-testid="rail-chat-content">CHAT SURFACE</div>;
}

function source(overrides: Partial<SourceEntry> = {}): SourceEntry {
  return {
    citation_id: "c1",
    source_connector: "notion",
    source_doc_id: "page_123",
    source_url: "https://example.com/notion/page_123",
    title: "Aurora 4.0 — Approved Positioning v3",
    snippet: "Aurora 4.0 brings agentic search to every desk.",
    freshness_at: null,
    citation_count: 1,
    last_cited_at: "2026-05-05T12:00:00Z",
    ...overrides,
  };
}

function sourceMap(entries: readonly SourceEntry[]): SourceEntryMap {
  return new Map(
    entries.map((e) => [`${e.source_connector} ${e.source_doc_id}`, e]),
  );
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

function approval(
  overrides: Partial<ApprovalsQueueItem> = {},
): ApprovalsQueueItem {
  return {
    approvalId: "ap-1",
    title: "Send renewal email",
    summary: null,
    approvalKind: "tool_action",
    runId: "run_1",
    messageId: "m1",
    resolved: false,
    resolvedAt: null,
    target: null,
    ...overrides,
  };
}

function approvalsQueue(
  pending: readonly ApprovalsQueueItem[] = [],
  recent: readonly ApprovalsQueueItem[] = [],
): ApprovalsQueueProjection {
  return { pending, recent };
}

/** The plain tab labels (reads the `__label` span so badges don't leak in). */
function tabLabels(): string[] {
  return screen
    .getAllByRole("tab")
    .map(
      (tab) =>
        tab.querySelector(".atlas-workspace-tabs__label")?.textContent ?? "",
    );
}

// ============================================================
// FR-3.10 — tab order, default, roles, arrow nav
// ============================================================

describe("RunWorkspaceRail — tabs (FR-3.10)", () => {
  it("renders exactly Chat · Sources · Agents · Approvals, in order", () => {
    render(<RunWorkspaceRail mode="studio" chatSlot={chatSlot()} />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(4);
    // The `.atlas-workspace-tabs__label` span carries the plain tab label.
    expect(tabLabels()).toEqual(["Chat", "Sources", "Agents", "Approvals"]);
  });

  it("selects Chat by default and hosts the injected chatSlot in its panel", () => {
    render(<RunWorkspaceRail mode="studio" chatSlot={chatSlot()} />);
    expect(screen.getByRole("tab", { name: "Chat" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    const chatPanel = screen.getByTestId("run-rail-panel-chat");
    expect(chatPanel).toHaveAttribute("role", "tabpanel");
    expect(
      within(chatPanel).getByTestId("rail-chat-content"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("run-workspace-rail")).toHaveAttribute(
      "data-active-tab",
      "chat",
    );
  });

  it("exposes a tablist with tab + tabpanel roles", () => {
    render(<RunWorkspaceRail mode="studio" chatSlot={chatSlot()} />);
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    expect(screen.getByRole("tabpanel")).toBeInTheDocument();
  });

  it("ArrowRight on the active tab advances selection (roving)", () => {
    render(<RunWorkspaceRail mode="studio" chatSlot={chatSlot()} />);
    fireEvent.keyDown(screen.getByRole("tab", { name: "Chat" }), {
      key: "ArrowRight",
    });
    expect(screen.getByRole("tab", { name: "Sources" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByTestId("run-workspace-rail")).toHaveAttribute(
      "data-active-tab",
      "sources",
    );
  });
});

// ============================================================
// FR-3.11 — Chat hosts TcChat; reuse WorkspacePane bodies; no Draft/Skills
// ============================================================

describe("RunWorkspaceRail — body reuse + omissions (FR-3.11)", () => {
  it("renders the hoisted SourcesTab body when Sources is selected", () => {
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        sources={sourceMap([source({ title: "Renewal terms" })])}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "Sources" }));
    expect(screen.getByTestId("workspace-sources-tab")).toBeInTheDocument();
    expect(screen.getByText("Renewal terms")).toBeInTheDocument();
  });

  it("renders the hoisted AgentsTab body when Agents is selected", () => {
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        subagents={subagentMap([subagent({ display_title: "Doc reader" })])}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: /Agents/ }));
    expect(screen.getByTestId("workspace-agents-tab")).toBeInTheDocument();
  });

  it("renders the hoisted ApprovalsTab body when Approvals is selected", () => {
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        approvalsQueue={approvalsQueue([approval({ title: "Send email" })])}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: /Approvals/ }));
    expect(screen.getByTestId("workspace-approvals-tab")).toBeInTheDocument();
    expect(screen.getByText("Send email")).toBeInTheDocument();
  });

  it("never renders Draft or Skills tabs", () => {
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        subagents={subagentMap([subagent()])}
        approvalsQueue={approvalsQueue([approval()])}
      />,
    );
    expect(tabLabels()).toEqual(["Chat", "Sources", "Agents", "Approvals"]);
    expect(screen.queryByRole("tab", { name: /Draft/ })).toBeNull();
    expect(screen.queryByRole("tab", { name: /Skills/ })).toBeNull();
  });

  it("routes the approvals jump callback through to the ApprovalsTab body", () => {
    const onJumpToApproval = vi.fn();
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        approvalsQueue={approvalsQueue([approval({ approvalId: "ap-9" })])}
        onJumpToApproval={onJumpToApproval}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: /Approvals/ }));
    fireEvent.click(screen.getByRole("button", { name: /Open approval/ }));
    expect(onJumpToApproval).toHaveBeenCalledWith("ap-9", "m1");
  });
});

// ============================================================
// FR-3.12 — count badges + per-tab empty copy
// ============================================================

describe("RunWorkspaceRail — badges + empty copy (FR-3.12)", () => {
  it("shows 'N live' on Agents while subagents are running", () => {
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        subagents={subagentMap([
          subagent({ task_id: "a", status: "running" }),
          subagent({ task_id: "b", status: "completed" }),
        ])}
      />,
    );
    const agentsTab = screen.getByRole("tab", { name: /Agents/ });
    expect(agentsTab).toHaveTextContent("1 live");
  });

  it("counts only in-flight subagents as live — a paused one is frozen, not live (FR-3.17c)", () => {
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        subagents={subagentMap([
          subagent({ task_id: "a", status: "running" }),
          subagent({ task_id: "b", status: "paused" }),
        ])}
      />,
    );
    const agentsTab = screen.getByRole("tab", { name: /Agents/ });
    // Two subagents exist, but only one is running → "1 live", not "2 live".
    expect(agentsTab).toHaveTextContent("1 live");
    expect(agentsTab).not.toHaveTextContent("2 live");
  });

  it("falls back to the total on Agents when nothing is running", () => {
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        subagents={subagentMap([
          subagent({ task_id: "a", status: "completed" }),
          subagent({ task_id: "b", status: "completed" }),
        ])}
      />,
    );
    const agentsTab = screen.getByRole("tab", { name: /Agents/ });
    expect(agentsTab).toHaveTextContent("2");
    expect(agentsTab).not.toHaveTextContent("live");
  });

  it("shows the pending count (accent) on Approvals", () => {
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        approvalsQueue={approvalsQueue([
          approval({ approvalId: "ap-1", messageId: "m1" }),
          approval({ approvalId: "ap-2", messageId: "m2" }),
        ])}
      />,
    );
    const badge = screen.getByTestId("run-rail-approvals-badge");
    expect(badge).toHaveTextContent("2");
    // Accent tone is a semantic marker, not a hardcoded hue (FR-3.12/3.24).
    expect(badge).toHaveAttribute("data-tone", "accent");
  });

  it("shows no Agents/Approvals badges when their counts are zero", () => {
    render(<RunWorkspaceRail mode="studio" chatSlot={chatSlot()} />);
    expect(screen.queryByTestId("run-rail-agents-badge")).toBeNull();
    expect(screen.queryByTestId("run-rail-approvals-badge")).toBeNull();
  });

  it("shows per-tab empty copy when a tab has no data", () => {
    render(<RunWorkspaceRail mode="studio" chatSlot={chatSlot()} />);

    fireEvent.click(screen.getByRole("tab", { name: "Sources" }));
    expect(
      screen.getByText(/Sources will appear here as Copilot finds them/),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Agents" }));
    expect(
      screen.getByTestId("workspace-agents-tab-empty"),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Approvals" }));
    expect(
      screen.getByTestId("workspace-approvals-tab-empty"),
    ).toBeInTheDocument();
  });
});

// ============================================================
// FR-3.13 — Focus mode collapses to Chat-only
// ============================================================

describe("RunWorkspaceRail — Focus mode (FR-3.13)", () => {
  it("suppresses the tab chrome and shows only the Chat surface", () => {
    render(
      <RunWorkspaceRail
        mode="focus"
        chatSlot={chatSlot()}
        subagents={subagentMap([subagent({ status: "running" })])}
        approvalsQueue={approvalsQueue([approval()])}
      />,
    );
    // No tablist / tabs in Focus.
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.queryAllByRole("tab")).toHaveLength(0);
    // The Chat surface is still mounted…
    expect(screen.getByTestId("rail-chat-content")).toBeInTheDocument();
    // …and the non-chat panels are gone even though their data is non-empty.
    expect(screen.queryByTestId("run-rail-panel-sources")).toBeNull();
    expect(screen.queryByTestId("run-rail-panel-agents")).toBeNull();
    expect(screen.queryByTestId("run-rail-panel-approvals")).toBeNull();
    expect(screen.getByTestId("run-workspace-rail")).toHaveAttribute(
      "data-mode",
      "focus",
    );
  });

  it("keeps the same Chat surface node across Studio→Focus (no remount)", () => {
    const { rerender } = render(
      <RunWorkspaceRail mode="studio" chatSlot={chatSlot()} />,
    );
    const before = screen.getByTestId("rail-chat-content");
    rerender(<RunWorkspaceRail mode="focus" chatSlot={chatSlot()} />);
    const after = screen.getByTestId("rail-chat-content");
    expect(after).toBe(before);
  });
});

// ============================================================
// PR-3.7 — approvals hidden while scrubbed (FR-3.15/3.16)
// ============================================================

describe("RunWorkspaceRail — scrubbed approvals gate (FR-3.15/3.16)", () => {
  it("drops the Approvals tab while scrubbed and flags the rail", () => {
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        approvalsQueue={approvalsQueue([approval()])}
        scrubbed
      />,
    );
    expect(tabLabels()).toEqual(["Chat", "Sources", "Agents"]);
    expect(screen.queryByRole("tab", { name: /Approvals/ })).toBeNull();
    expect(screen.getByTestId("run-workspace-rail")).toHaveAttribute(
      "data-approvals-hidden",
      "true",
    );
  });

  it("restores the Approvals tab when snapped back to live", () => {
    const { rerender } = render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        approvalsQueue={approvalsQueue([approval()])}
        scrubbed
      />,
    );
    expect(screen.queryByRole("tab", { name: /Approvals/ })).toBeNull();

    rerender(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        approvalsQueue={approvalsQueue([approval()])}
      />,
    );
    expect(screen.getByRole("tab", { name: /Approvals/ })).toBeInTheDocument();
    expect(screen.getByTestId("run-workspace-rail")).toHaveAttribute(
      "data-approvals-hidden",
      "false",
    );
  });

  it("falls back to Chat when Approvals was active and the run is scrubbed", () => {
    const { rerender } = render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        approvalsQueue={approvalsQueue([approval()])}
      />,
    );
    // Select Approvals while live…
    fireEvent.click(screen.getByRole("tab", { name: /Approvals/ }));
    expect(screen.getByTestId("run-workspace-rail")).toHaveAttribute(
      "data-active-tab",
      "approvals",
    );

    // …then scrub: the panel gives way to Chat (its tab is gone).
    rerender(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        approvalsQueue={approvalsQueue([approval()])}
        scrubbed
      />,
    );
    expect(screen.getByTestId("run-workspace-rail")).toHaveAttribute(
      "data-active-tab",
      "chat",
    );
    expect(screen.getByTestId("rail-chat-content")).toBeInTheDocument();
    expect(screen.queryByTestId("run-rail-panel-approvals")).toBeNull();
  });
});
