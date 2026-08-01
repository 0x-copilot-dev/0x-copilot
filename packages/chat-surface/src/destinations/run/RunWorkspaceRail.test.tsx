// RunWorkspaceRail — tabbed right-rail tests (PR-3.6).
//
// Covers the FRs the rail owns:
//   FR-3.10 — tab order `Chat · Agents · Approvals · Sources` (v3), Chat
//             default, role="tablist"/tab/tabpanel, arrow-key nav.
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
import type {
  SourceEntry,
  SourcesProjectionV2,
  SubagentEntry,
} from "@0x-copilot/api-types";

import type {
  ApprovalsQueueItem,
  ApprovalsQueueProjection,
  SourceEntryMap,
  SubagentSnapshotMap,
} from "../../workspace";
import type { SubagentActivityRecord } from "../../subagents";
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
  it("renders exactly Chat · Agents · Approvals · Sources, in order", () => {
    render(<RunWorkspaceRail mode="studio" chatSlot={chatSlot()} />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(4);
    // The `.atlas-workspace-tabs__label` span carries the plain tab label.
    expect(tabLabels()).toEqual(["Chat", "Agents", "Approvals", "Sources"]);
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

  // Regression: the Studio chat panel was `overflow: auto`, so a resting
  // composer tooltip overflowing the column's right edge made the transcript
  // AND the composer draggable sideways as one. A chat column scrolls the way a
  // transcript does — vertically — and clips horizontally; anything genuinely
  // wide (code, tables) owns its own scroller.
  it("never scrolls the chat column horizontally", () => {
    render(<RunWorkspaceRail mode="studio" chatSlot={chatSlot()} />);
    expect(screen.getByTestId("run-rail-panel-chat")).toHaveStyle({
      overflowX: "hidden",
      overflowY: "auto",
    });
  });

  it("reserves Focus-only breathing room below the active composer", () => {
    const { rerender } = render(
      <RunWorkspaceRail mode="focus" chatSlot={chatSlot()} />,
    );
    expect(screen.getByTestId("run-rail-panel-chat")).toHaveStyle({
      boxSizing: "border-box",
      paddingBottom: "16px",
    });

    rerender(<RunWorkspaceRail mode="studio" chatSlot={chatSlot()} />);
    expect(screen.getByTestId("run-rail-panel-chat")).not.toHaveStyle({
      paddingBottom: "16px",
    });
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
    // v3 order: the tab after Chat is Agents.
    expect(screen.getByRole("tab", { name: /Agents/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByTestId("run-workspace-rail")).toHaveAttribute(
      "data-active-tab",
      "agents",
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

  it("swaps in the ledger LedgerSourcesTab when ledgerSources is non-null (PRD-E1)", () => {
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        sources={sourceMap([source({ title: "Renewal terms" })])}
        ledgerSources={{
          total: 1,
          groups: [
            {
              connector: "linear",
              rows: [
                {
                  op: "get_issue",
                  title: "ENG-142",
                  at: "2026-01-01T00:00:04Z",
                  ledgerId: "rrun·004",
                  latencyMs: 12,
                  qualifier: "auto-ran (read)",
                },
              ],
            },
          ],
        }}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "Sources" }));
    // The v2 ledger body replaces the legacy citation body entirely.
    expect(screen.getByTestId("ledger-sources-tab")).toBeInTheDocument();
    expect(screen.queryByTestId("workspace-sources-tab")).toBeNull();
    expect(screen.queryByText("Renewal terms")).toBeNull();
  });

  it("gives canonical Sources v2 precedence and forwards only its opaque id", () => {
    const onOpenSource = vi.fn();
    const sourcesV2: SourcesProjectionV2 = {
      v: 2,
      run_id: "run_1",
      latest_sequence_no: 4,
      facts: [
        {
          source_id: "source:v2:004:artifact",
          kind: "artifact",
          sequence_no: 4,
          ledger_id: "rrun0000·004",
          connector: null,
          tool: null,
          origin: null,
          artifact_id: "art_safe_target",
          artifact_revision: 1,
          artifact_source_ref: "artifact://art_safe_target/revisions/1",
          workspace_grant_label: null,
          workspace_virtual_path_key: null,
          browser_origin: null,
          sandbox_operation: null,
          subagent_task: null,
          external_receipt_ref: null,
        },
      ],
    };
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        ledgerSources={{ total: 0, groups: [] }}
        sourcesV2={{
          projection: sourcesV2,
          onOpenSource,
          openingSourceId: null,
          openMessage: null,
        }}
      />,
    );

    fireEvent.click(screen.getByRole("tab", { name: "Sources" }));
    expect(screen.getByTestId("sources-v2-tab")).toBeInTheDocument();
    expect(screen.queryByTestId("ledger-sources-tab")).toBeNull();
    // Opening is the row's title button now (the trailing glyph is gone with
    // the compact card); find it by the row that is owner-routed openable.
    const openable = screen
      .getAllByTestId("sources-v2-row")
      .find((r) => r.getAttribute("data-openable") === "true");
    fireEvent.click(within(openable as HTMLElement).getByRole("button"));
    expect(onOpenSource).toHaveBeenCalledWith("source:v2:004:artifact");
  });

  it("keeps the legacy SourcesTab when ledgerSources is null (byte-identical)", () => {
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        sources={sourceMap([source({ title: "Renewal terms" })])}
        ledgerSources={null}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "Sources" }));
    expect(screen.getByTestId("workspace-sources-tab")).toBeInTheDocument();
    expect(screen.queryByTestId("ledger-sources-tab")).toBeNull();
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

  it("passes canonical per-task activity into the Agents disclosure", () => {
    const activitiesByTask: ReadonlyMap<
      string,
      readonly SubagentActivityRecord[]
    > = new Map([
      [
        "task_a",
        [
          {
            id: "call-search",
            kind: "tool",
            title: "web_search",
            status: "completed",
            summary: "Found 3 primary sources",
            inputSummary: null,
            result: "Found 3 primary sources",
            isError: false,
          },
        ],
      ],
    ]);
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        defaultTab="agents"
        subagents={subagentMap([subagent({ task_id: "task_a" })])}
        subagentActivitiesByTask={activitiesByTask}
      />,
    );

    const details = screen.getByTestId(
      "agent-activity-row-details-task_a",
    ) as HTMLDetailsElement;
    fireEvent.click(details.querySelector("summary")!);
    expect(
      screen.getByRole("region", { name: "Doc reader activity details" }),
    ).toHaveTextContent("Found 3 primary sources");
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
    expect(tabLabels()).toEqual(["Chat", "Agents", "Approvals", "Sources"]);
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
// WS-F — Focus mode: Chat | Run-details two-column layout
// ============================================================

describe("RunWorkspaceRail — Focus Run-details panel (WS-F)", () => {
  it("drops the Studio tabset and shows the Chat column + the Run-details panel", () => {
    render(
      <RunWorkspaceRail
        mode="focus"
        chatSlot={chatSlot()}
        subagents={subagentMap([subagent({ status: "running" })])}
        approvalsQueue={approvalsQueue([approval()])}
      />,
    );
    // The Studio 4-tab tablist (incl. a Chat tab) is gone…
    expect(
      screen.queryByRole("tablist", { name: "Run workspace tabs" }),
    ).toBeNull();
    expect(screen.queryByRole("tab", { name: "Chat" })).toBeNull();
    // …the Chat surface is the LEFT column (still mounted)…
    expect(screen.getByTestId("rail-chat-content")).toBeInTheDocument();
    // …and the Run-details panel is the RIGHT column, defaulting to Agents.
    const panel = screen.getByTestId("tc-focus-panel");
    expect(panel).toBeInTheDocument();
    expect(within(panel).getByRole("tablist")).toBeInTheDocument();
    // Agents / Approvals / Sources are the SideTabs (no Chat tab).
    expect(tabLabels()).toEqual(["Agents", "Approvals", "Sources"]);
    expect(screen.getByTestId("run-workspace-rail")).toHaveAttribute(
      "data-focus-panel-tab",
      "agents",
    );
  });

  it("renders the truthful live cue in the Activity panel", () => {
    render(
      <RunWorkspaceRail mode="focus" chatSlot={chatSlot()} focusActivityLive />,
    );

    const panel = screen.getByTestId("tc-focus-panel");
    expect(within(panel).getByText("Activity")).toBeInTheDocument();
    expect(within(panel).getByTestId("tc-focus-panel-live")).toHaveTextContent(
      "live",
    );
  });

  it("reuses the hoisted Agents/Approvals/Sources bodies in the Run-details panel", () => {
    render(
      <RunWorkspaceRail
        mode="focus"
        chatSlot={chatSlot()}
        subagents={subagentMap([subagent({ display_title: "Doc reader" })])}
        approvalsQueue={approvalsQueue([approval({ title: "Send email" })])}
        sources={sourceMap([source({ title: "Renewal terms" })])}
      />,
    );
    // Default → Agents body reachable.
    expect(screen.getByTestId("workspace-agents-tab")).toBeInTheDocument();
    // Switch to Approvals → the hoisted ApprovalsTab body.
    fireEvent.click(screen.getByRole("tab", { name: /Approvals/ }));
    expect(screen.getByTestId("workspace-approvals-tab")).toBeInTheDocument();
    expect(screen.getByText("Send email")).toBeInTheDocument();
    // Switch to Sources → the hoisted SourcesTab body.
    fireEvent.click(screen.getByRole("tab", { name: "Sources" }));
    expect(screen.getByTestId("workspace-sources-tab")).toBeInTheDocument();
    expect(screen.getByText("Renewal terms")).toBeInTheDocument();
    // Chat is NOT hidden away — it stays the left column throughout.
    expect(screen.getByTestId("rail-chat-content")).toBeInTheDocument();
  });

  it("keeps the Approvals pending badge in the Run-details panel", () => {
    render(
      <RunWorkspaceRail
        mode="focus"
        chatSlot={chatSlot()}
        approvalsQueue={approvalsQueue([
          approval({ approvalId: "ap-1", messageId: "m1" }),
          approval({ approvalId: "ap-2", messageId: "m2" }),
        ])}
      />,
    );
    const badge = screen.getByTestId("run-rail-approvals-badge");
    expect(badge).toHaveTextContent("2");
    expect(badge).toHaveAttribute("data-tone", "accent");
  });

  it("collapses to the 46px icon rail and expands back", () => {
    render(<RunWorkspaceRail mode="focus" chatSlot={chatSlot()} />);
    // Expanded by default.
    expect(screen.getByTestId("tc-focus-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("tc-focus-strip")).toBeNull();

    // Collapse → the icon rail replaces the panel.
    fireEvent.click(screen.getByTestId("tc-focus-panel-collapse"));
    expect(screen.queryByTestId("tc-focus-panel")).toBeNull();
    const strip = screen.getByTestId("tc-focus-strip");
    expect(strip).toBeInTheDocument();
    expect(screen.getByTestId("run-workspace-rail")).toHaveAttribute(
      "data-focus-panel-collapsed",
      "true",
    );
    // The Chat column survives the collapse.
    expect(screen.getByTestId("rail-chat-content")).toBeInTheDocument();

    // Expand → back to the full panel.
    fireEvent.click(screen.getByTestId("tc-focus-strip-expand"));
    expect(screen.getByTestId("tc-focus-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("tc-focus-strip")).toBeNull();
  });

  it("a collapsed icon click expands the panel onto that tab", () => {
    render(
      <RunWorkspaceRail
        mode="focus"
        chatSlot={chatSlot()}
        sources={sourceMap([source({ title: "Renewal terms" })])}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-focus-panel-collapse"));
    // Click the Sources icon in the rail → expand + select Sources.
    fireEvent.click(screen.getByTestId("tc-focus-strip-sources"));
    expect(screen.getByTestId("tc-focus-panel")).toBeInTheDocument();
    expect(screen.getByTestId("run-workspace-rail")).toHaveAttribute(
      "data-focus-panel-tab",
      "sources",
    );
    expect(screen.getByText("Renewal terms")).toBeInTheDocument();
  });

  it("shows an accent badge on the collapsed Approvals icon when pending", () => {
    render(
      <RunWorkspaceRail
        mode="focus"
        chatSlot={chatSlot()}
        approvalsQueue={approvalsQueue([approval()])}
        panelCollapsed
      />,
    );
    const badge = screen.getByTestId("tc-focus-strip-approvals-badge");
    expect(badge).toHaveTextContent("1");
    expect(badge).toHaveAttribute("data-tone", "accent");
  });

  it("honors a controlled panelCollapsed prop and reports the toggle", () => {
    const onPanelCollapsedChange = vi.fn();
    const { rerender } = render(
      <RunWorkspaceRail
        mode="focus"
        chatSlot={chatSlot()}
        panelCollapsed={false}
        onPanelCollapsedChange={onPanelCollapsedChange}
      />,
    );
    expect(screen.getByTestId("tc-focus-panel")).toBeInTheDocument();
    // Controlled: clicking reports up but does not self-collapse.
    fireEvent.click(screen.getByTestId("tc-focus-panel-collapse"));
    expect(onPanelCollapsedChange).toHaveBeenCalledWith(true);
    expect(screen.getByTestId("tc-focus-panel")).toBeInTheDocument();
    // Host flips the prop → the rail collapses.
    rerender(
      <RunWorkspaceRail
        mode="focus"
        chatSlot={chatSlot()}
        panelCollapsed
        onPanelCollapsedChange={onPanelCollapsedChange}
      />,
    );
    expect(screen.getByTestId("tc-focus-strip")).toBeInTheDocument();
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

  it("drops the Approvals SideTab while scrubbed off-now", () => {
    render(
      <RunWorkspaceRail
        mode="focus"
        chatSlot={chatSlot()}
        approvalsQueue={approvalsQueue([approval()])}
        scrubbed
      />,
    );
    expect(tabLabels()).toEqual(["Agents", "Sources"]);
    expect(screen.queryByRole("tab", { name: /Approvals/ })).toBeNull();
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
    expect(tabLabels()).toEqual(["Chat", "Agents", "Sources"]);
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

// ============================================================
// Following a citation — `focusSourcesSignal`
// ============================================================
//
// Clicking an inline `[[N]]` chip must reveal the source it points at. The
// cockpit drives that through a one-directional nonce, mirroring the header
// chip's `focusApprovalsSignal`. Before this existed the chip was inert on both
// hosts, so these cases pin the whole point of a citation: it is followable.

describe("RunWorkspaceRail — focusSourcesSignal", () => {
  it("ignores the initial value so mounting never force-selects Sources", () => {
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        focusSourcesSignal={7}
      />,
    );
    expect(screen.getByTestId("run-workspace-rail")).toHaveAttribute(
      "data-active-tab",
      "chat",
    );
  });

  it("selects Sources in Studio when the nonce increases", () => {
    const { rerender } = render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        sources={sourceMap([source()])}
        focusSourcesSignal={0}
      />,
    );
    rerender(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        sources={sourceMap([source()])}
        focusSourcesSignal={1}
      />,
    );
    expect(screen.getByTestId("run-workspace-rail")).toHaveAttribute(
      "data-active-tab",
      "sources",
    );
    expect(screen.getByRole("tab", { name: "Sources" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("does not fight the reader's own tab clicks after the nonce fires", () => {
    const { rerender } = render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        sources={sourceMap([source()])}
        focusSourcesSignal={0}
      />,
    );
    rerender(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        sources={sourceMap([source()])}
        focusSourcesSignal={1}
      />,
    );
    // The reader navigates away; a re-render at the SAME nonce must not yank
    // them back to Sources (the effect keys on the nonce, not on every render).
    fireEvent.click(screen.getByRole("tab", { name: /Agents/ }));
    rerender(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        sources={sourceMap([source()])}
        focusSourcesSignal={1}
      />,
    );
    expect(screen.getByTestId("run-workspace-rail")).toHaveAttribute(
      "data-active-tab",
      "agents",
    );
  });

  it("expands a collapsed Focus panel so the reveal is not swallowed", () => {
    const onPanelCollapsedChange = vi.fn();
    const { rerender } = render(
      <RunWorkspaceRail
        mode="focus"
        chatSlot={chatSlot()}
        sources={sourceMap([source()])}
        panelCollapsed
        onPanelCollapsedChange={onPanelCollapsedChange}
        focusSourcesSignal={0}
      />,
    );
    rerender(
      <RunWorkspaceRail
        mode="focus"
        chatSlot={chatSlot()}
        sources={sourceMap([source()])}
        panelCollapsed
        onPanelCollapsedChange={onPanelCollapsedChange}
        focusSourcesSignal={1}
      />,
    );
    // Selecting a tab inside a collapsed panel would read as a dead chip.
    expect(onPanelCollapsedChange).toHaveBeenCalledWith(false);
  });

  it("leaves the Focus collapse state alone in Studio", () => {
    const onPanelCollapsedChange = vi.fn();
    const { rerender } = render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        sources={sourceMap([source()])}
        panelCollapsed
        onPanelCollapsedChange={onPanelCollapsedChange}
        focusSourcesSignal={0}
      />,
    );
    rerender(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        sources={sourceMap([source()])}
        panelCollapsed
        onPanelCollapsedChange={onPanelCollapsedChange}
        focusSourcesSignal={1}
      />,
    );
    expect(onPanelCollapsedChange).not.toHaveBeenCalled();
  });
});

// ── the v2 Sources panel must still show cited documents ─────────────────────

describe("RunWorkspaceRail — citations reach the v2 Sources panel", () => {
  const V2_EMPTY = {
    projection: {
      v: 2 as const,
      run_id: "run_1",
      latest_sequence_no: 0,
      facts: [],
    },
    onOpenSource: () => {},
    openingSourceId: null,
    openMessage: null,
  };

  it("injects cited documents into SourcesV2Tab when the ledger fold is empty", () => {
    // The shipped bug: surfacesV2 defaults ON, so this branch renders, and its
    // fold knows nothing about citations — a web search left Sources blank.
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        sources={sourceMap([source()])}
        sourcesV2={V2_EMPTY}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "Sources" }));
    expect(screen.getByTestId("sources-v2-citations")).toBeInTheDocument();
    expect(
      screen.getByText("Aurora 4.0 — Approved Positioning v3"),
    ).toBeInTheDocument();
  });

  it("shows the v2 empty state when there are no citations either", () => {
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        sourcesV2={V2_EMPTY}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "Sources" }));
    expect(screen.getByTestId("sources-v2-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("sources-v2-citations")).toBeNull();
  });
});

// ============================================================
// Studio rail fold — the chevron + the icon strip
// ============================================================

describe("RunWorkspaceRail — Studio rail fold", () => {
  it("renders a collapse chevron in the Studio tabset", () => {
    render(<RunWorkspaceRail mode="studio" chatSlot={chatSlot()} />);
    const chevron = screen.getByTestId("run-rail-collapse");
    expect(chevron).toHaveAttribute("aria-label", "Collapse workspace rail");
    expect(chevron).toHaveAttribute("aria-expanded", "true");
  });

  it("has no collapse chevron in Focus (that mode has its own panel control)", () => {
    render(<RunWorkspaceRail mode="focus" chatSlot={chatSlot()} />);
    expect(screen.queryByTestId("run-rail-collapse")).toBeNull();
  });

  it("folds to the icon strip when the chevron is clicked (uncontrolled)", () => {
    render(<RunWorkspaceRail mode="studio" chatSlot={chatSlot()} />);
    fireEvent.click(screen.getByTestId("run-rail-collapse"));

    expect(screen.getByTestId("run-workspace-rail")).toHaveAttribute(
      "data-studio-collapsed",
      "true",
    );
    expect(screen.getByTestId("run-rail-strip")).toBeInTheDocument();
    // The tabset and its chevron give way to the strip.
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.queryByTestId("run-rail-collapse")).toBeNull();
  });

  // The whole point of the fold: a folded rail must actually be narrow, or the
  // surface column gets nothing back.
  it("caps the folded rail at the icon-strip width", () => {
    render(<RunWorkspaceRail mode="studio" chatSlot={chatSlot()} />);
    fireEvent.click(screen.getByTestId("run-rail-collapse"));
    expect(screen.getByTestId("run-workspace-rail")).toHaveStyle({
      width: "46px",
    });
  });

  // The contract that makes folding safe: `chatSlot` is hidden, never unmounted,
  // so transcript scroll + composer draft survive a fold/unfold round trip.
  it("keeps the injected chatSlot mounted (hidden) while folded", () => {
    const { rerender } = render(
      <RunWorkspaceRail mode="studio" chatSlot={chatSlot()} />,
    );
    const before = screen.getByTestId("rail-chat-content");
    fireEvent.click(screen.getByTestId("run-rail-collapse"));

    const chatPanel = screen.getByTestId("run-rail-panel-chat");
    expect(chatPanel).toHaveStyle({ display: "none" });
    expect(screen.getByTestId("rail-chat-content")).toBe(before);

    rerender(<RunWorkspaceRail mode="studio" chatSlot={chatSlot()} />);
    fireEvent.click(screen.getByTestId("run-rail-expand"));
    expect(screen.getByTestId("run-rail-panel-chat")).toHaveStyle({
      display: "flex",
    });
    expect(screen.getByTestId("rail-chat-content")).toBe(before);
  });

  it("expands again from the strip's chevron", () => {
    render(<RunWorkspaceRail mode="studio" chatSlot={chatSlot()} />);
    fireEvent.click(screen.getByTestId("run-rail-collapse"));
    const expand = screen.getByTestId("run-rail-expand");
    expect(expand).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(expand);
    expect(screen.getByTestId("run-workspace-rail")).toHaveAttribute(
      "data-studio-collapsed",
      "false",
    );
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    expect(screen.queryByTestId("run-rail-strip")).toBeNull();
  });

  // One click should always land on something visible — picking an icon selects
  // that tab AND unfolds, rather than silently selecting behind the strip.
  it("selects a tab and unfolds when a strip icon is picked", () => {
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        sources={sourceMap([source({ title: "Renewal terms" })])}
      />,
    );
    fireEvent.click(screen.getByTestId("run-rail-collapse"));
    fireEvent.click(screen.getByTestId("run-rail-strip-sources"));

    expect(screen.getByTestId("run-workspace-rail")).toHaveAttribute(
      "data-active-tab",
      "sources",
    );
    expect(screen.getByTestId("run-rail-panel-sources")).toBeInTheDocument();
    expect(screen.queryByTestId("run-rail-strip")).toBeNull();
  });

  it("marks the tab the strip will restore", () => {
    render(<RunWorkspaceRail mode="studio" chatSlot={chatSlot()} />);
    fireEvent.click(screen.getByRole("tab", { name: /Agents/ }));
    fireEvent.click(screen.getByTestId("run-rail-collapse"));
    expect(screen.getByTestId("run-rail-strip-agents")).toHaveAttribute(
      "data-active",
      "true",
    );
    expect(screen.getByTestId("run-rail-strip-chat")).toHaveAttribute(
      "data-active",
      "false",
    );
  });

  it("carries the Agents live count + Approvals pending count into the strip", () => {
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        subagents={subagentMap([
          subagent({ task_id: "t1", status: "running" }),
          subagent({ task_id: "t2", status: "completed" }),
        ])}
        approvalsQueue={approvalsQueue([approval()])}
      />,
    );
    fireEvent.click(screen.getByTestId("run-rail-collapse"));
    expect(screen.getByTestId("run-rail-strip-agents-badge")).toHaveTextContent(
      "1",
    );
    expect(
      screen.getByTestId("run-rail-strip-approvals-badge"),
    ).toHaveTextContent("1");
  });

  // FR-3.15 parity: you cannot approve a past state, so the folded rail must not
  // offer an Approvals icon either.
  it("drops the Approvals icon from the strip while scrubbed", () => {
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        approvalsQueue={approvalsQueue([approval()])}
        scrubbed
      />,
    );
    fireEvent.click(screen.getByTestId("run-rail-collapse"));
    expect(screen.queryByTestId("run-rail-strip-approvals")).toBeNull();
    expect(screen.getByTestId("run-rail-strip-sources")).toBeInTheDocument();
  });

  it("is controlled when the host supplies studioCollapsed", () => {
    const onStudioCollapsedChange = vi.fn();
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        studioCollapsed={false}
        onStudioCollapsedChange={onStudioCollapsedChange}
      />,
    );
    fireEvent.click(screen.getByTestId("run-rail-collapse"));
    expect(onStudioCollapsedChange).toHaveBeenCalledWith(true);
    // Controlled: the rail does not fold itself — the host's next value does.
    expect(screen.getByTestId("run-workspace-rail")).toHaveAttribute(
      "data-studio-collapsed",
      "false",
    );
  });

  it("does not touch the Focus panel's collapse state", () => {
    const onPanelCollapsedChange = vi.fn();
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        panelCollapsed={false}
        onPanelCollapsedChange={onPanelCollapsedChange}
      />,
    );
    fireEvent.click(screen.getByTestId("run-rail-collapse"));
    expect(onPanelCollapsedChange).not.toHaveBeenCalled();
  });

  // A citation chip / "N waiting" chip that selects a tab behind a folded rail
  // reads as a dead click.
  it("unfolds when a citation chip commands the Sources tab", () => {
    const onStudioCollapsedChange = vi.fn();
    const { rerender } = render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        studioCollapsed
        onStudioCollapsedChange={onStudioCollapsedChange}
        focusSourcesSignal={0}
      />,
    );
    rerender(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        studioCollapsed
        onStudioCollapsedChange={onStudioCollapsedChange}
        focusSourcesSignal={1}
      />,
    );
    expect(onStudioCollapsedChange).toHaveBeenCalledWith(false);
  });

  it("unfolds when the header chip commands the Approvals tab", () => {
    const onStudioCollapsedChange = vi.fn();
    const { rerender } = render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        studioCollapsed
        onStudioCollapsedChange={onStudioCollapsedChange}
        focusApprovalsSignal={0}
      />,
    );
    rerender(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        studioCollapsed
        onStudioCollapsedChange={onStudioCollapsedChange}
        focusApprovalsSignal={1}
      />,
    );
    expect(onStudioCollapsedChange).toHaveBeenCalledWith(false);
  });

  // Mounting with a non-zero nonce (a remount mid-session) must not yank a
  // deliberately-folded rail open.
  it("does not unfold on a nonce that merely arrives non-zero", () => {
    const onStudioCollapsedChange = vi.fn();
    render(
      <RunWorkspaceRail
        mode="studio"
        chatSlot={chatSlot()}
        studioCollapsed
        onStudioCollapsedChange={onStudioCollapsedChange}
        focusApprovalsSignal={7}
        focusSourcesSignal={7}
      />,
    );
    expect(onStudioCollapsedChange).not.toHaveBeenCalled();
  });
});
