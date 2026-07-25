// PR 3.2.1 — disclosure UX coverage for the Agents tab.
// PR-1.7 — moved down with the component; the same assertions run from
// chat-surface. The host `chatModel` map helpers are reproduced inline here so
// the test stays app-import-free.
//
// Closed by default (AC-2). Open reveals SubagentActivityList rows
// (AC-3). focusTaskId auto-opens (AC-2 supporting). Empty-activities
// fallback renders (AC-6). The "↗ jump to thread" button still works
// alongside the disclosure (AC-5 supporting). A failed status surfaces
// the danger badge tone (AC-12).

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type {
  SubagentEntry,
  SubagentLifecycleStatus,
} from "@0x-copilot/api-types";
import type { SubagentActivityRecord } from "../subagents";
import { AgentsTab } from "./AgentsTab";
import type { SubagentSnapshotMap } from "./workspaceHelpers";

function entry(overrides: Partial<SubagentEntry> = {}): SubagentEntry {
  return {
    task_id: "task_doc_reader",
    parent_run_id: "run_1",
    subagent_name: "doc_reader",
    status: "completed",
    display_title: "Doc reader",
    objective_summary: "Read positioning + GTM plan, extract claims",
    started_at: "2026-05-06T10:00:00Z",
    completed_at: "2026-05-06T10:00:18Z",
    duration_ms: 18000,
    result_summary:
      "Hero claim: time-to-answer + citation trust. Key proof points pulled into draft.",
    safe_error_code: null,
    safe_error_message: null,
    token_usage: null,
    ...overrides,
  };
}

function emptySubagentMap(): SubagentSnapshotMap {
  return new Map();
}

function seedSubagentMap(
  entries: readonly SubagentEntry[],
): SubagentSnapshotMap {
  return new Map(entries.map((e) => [e.task_id, e]));
}

function activity(
  overrides: Partial<SubagentActivityRecord> = {},
): SubagentActivityRecord {
  return {
    id: "call_a",
    kind: "tool",
    title: "search_notion",
    status: "completed",
    summary: "4 hits",
    inputSummary: null,
    result: null,
    isError: false,
    ...overrides,
  };
}

function activityMap(
  byTask: Record<string, SubagentActivityRecord[]>,
): ReadonlyMap<string, readonly SubagentActivityRecord[]> {
  return new Map(Object.entries(byTask));
}

describe("AgentsTab Focus rows", () => {
  it("renders the empty hint when no subagents have been dispatched", () => {
    render(<AgentsTab subagents={emptySubagentMap()} />);
    expect(
      screen.getByText(
        /Subagents run here when Copilot dispatches parallel work/,
      ),
    ).toBeInTheDocument();
  });

  it("renders one explicit native detail control per subagent, closed by default", () => {
    const subagents = seedSubagentMap([entry()]);
    render(<AgentsTab subagents={subagents} />);
    const details = screen.getByTestId(
      "agent-activity-row-details-task_doc_reader",
    ) as HTMLDetailsElement;
    expect(details.open).toBe(false);
    expect(details.querySelector("summary")).toHaveAttribute(
      "aria-label",
      "Toggle Doc reader activity details",
    );
  });

  it("renders thread-derived history groups even when the snapshot is empty", async () => {
    const onJumpToSubagent = vi.fn();
    render(
      <AgentsTab
        subagents={emptySubagentMap()}
        onJumpToSubagent={onJumpToSubagent}
        historyGroups={[
          {
            id: "run_1",
            label: "1 subagent dispatched",
            timestamp: "2026-05-06T10:00:00Z",
            entries: [entry()],
          },
        ]}
      />,
    );
    expect(screen.getByText("1 subagent dispatched")).toBeInTheDocument();
    // PR 4.4.7 — display_title drives the row name verbatim.
    expect(screen.getByText("Doc reader")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByText("1 subagent dispatched"));
    expect(onJumpToSubagent).toHaveBeenCalledWith(
      expect.objectContaining({ task_id: "task_doc_reader" }),
    );
  });

  it("renders the timeline rows when the disclosure is opened", async () => {
    const user = userEvent.setup();
    const subagents = seedSubagentMap([entry()]);
    render(
      <AgentsTab
        subagents={subagents}
        activitiesByTask={activityMap({
          task_doc_reader: [
            activity({
              id: "call_a",
              title: "search_notion",
              summary: "4 hits",
            }),
            activity({
              id: "call_b",
              title: "read_file",
              summary: "GTM/FY26-Q1 plan",
            }),
          ],
        })}
      />,
    );
    const details = screen.getByTestId(
      "agent-activity-row-details-task_doc_reader",
    ) as HTMLDetailsElement;
    await user.click(details.querySelector("summary")!);
    expect(details.open).toBe(true);
    const region = screen.getByRole("region", {
      name: "Doc reader activity details",
    });
    expect(
      region.querySelectorAll(".aui-tool-card__timeline-item").length,
    ).toBe(2);
    expect(region).toHaveTextContent("4 hits");
    expect(region).toHaveTextContent("GTM/FY26-Q1 plan");
  });

  it("auto-opens the focused subagent's explicit native detail region", () => {
    const subagents = seedSubagentMap([entry()]);
    render(
      <AgentsTab
        subagents={subagents}
        focusTaskId="task_doc_reader"
        activitiesByTask={activityMap({
          task_doc_reader: [activity()],
        })}
      />,
    );
    const details = screen.getByTestId(
      "agent-activity-row-details-task_doc_reader",
    ) as HTMLDetailsElement;
    expect(details.open).toBe(true);
    expect(
      screen.getByRole("region", { name: "Doc reader activity details" }),
    ).toBeInTheDocument();
  });

  it("falls back to the empty-activity message when the subagent has no inner steps and no result text", async () => {
    const user = userEvent.setup();
    // PR 3.2.2 AC-5 — empty disclosure body needs activities=[] AND
    // result_summary=null. With result_summary set the disclosure shows
    // the full result instead (AC-4 — covered in SubagentCard.test).
    const subagents = seedSubagentMap([entry({ result_summary: null })]);
    render(
      <AgentsTab
        subagents={subagents}
        activitiesByTask={activityMap({ task_doc_reader: [] })}
      />,
    );
    await user.click(
      screen
        .getByTestId("agent-activity-row-details-task_doc_reader")
        .querySelector("summary")!,
    );
    expect(
      screen.getByText(/Single-shot response — no inner tool calls\./),
    ).toBeInTheDocument();
  });

  it("composes the workspace-narrow timeline class on top of the in-thread base class", async () => {
    const subagents = seedSubagentMap([entry()]);
    const { container } = render(
      <AgentsTab
        subagents={subagents}
        activitiesByTask={activityMap({
          task_doc_reader: [activity()],
        })}
      />,
    );
    await userEvent
      .setup()
      .click(
        screen
          .getByTestId("agent-activity-row-details-task_doc_reader")
          .querySelector("summary")!,
      );
    const timeline = container.querySelector(
      ".atlas-workspace-agent__timeline",
    );
    expect(timeline).not.toBeNull();
    expect(timeline?.classList.contains("aui-tool-card__timeline")).toBe(true);
  });

  it("still surfaces the jump-to-thread button alongside the disclosure", async () => {
    const onJumpToSubagent = vi.fn();
    const subagents = seedSubagentMap([entry()]);
    render(
      <AgentsTab
        subagents={subagents}
        onJumpToSubagent={onJumpToSubagent}
        activitiesByTask={activityMap({
          task_doc_reader: [activity()],
        })}
      />,
    );
    const user = userEvent.setup();
    await user.click(
      // PR 4.4.7 — display_title now drives the name; the entry fixture
      // sets it to "Doc reader" (sentence case), which flows verbatim
      // into the aria-label.
      screen.getByRole("button", { name: /Open Doc reader in thread/ }),
    );
    expect(onJumpToSubagent).toHaveBeenCalledOnce();
  });

  it("renders a compact accessible lifecycle glyph for failed subagents", () => {
    const status: SubagentLifecycleStatus = "failed";
    const subagents = seedSubagentMap([entry({ status, duration_ms: 4200 })]);
    render(<AgentsTab subagents={subagents} />);
    expect(screen.getByRole("img", { name: "Failed" })).toBeInTheDocument();
  });

  it("keeps legacy objective text as the quiet activity fallback", () => {
    const subagents = seedSubagentMap([
      entry({ status: "running", duration_ms: null, completed_at: null }),
    ]);
    render(<AgentsTab subagents={subagents} />);
    expect(
      screen.getByText("Read positioning + GTM plan, extract claims"),
    ).toBeInTheDocument();
  });

  it("renders a synthetic explicit parent lead for non-orchestrator role hints", () => {
    const orchestrated = {
      ...entry({
        task_id: "task_research",
        display_title: "Research agent",
        status: "running",
        completed_at: null,
        duration_ms: null,
      }),
      parent_task_id: "main-agent",
      parent_agent_role: "planner",
      model_display_label: "Haiku 4.5",
      current_activity: "Searching trusted source material",
    } as SubagentEntry;
    render(<AgentsTab subagents={seedSubagentMap([orchestrated])} />);

    expect(screen.getByText("Planner")).toBeInTheDocument();
    expect(screen.getByText("Haiku 4.5")).toBeInTheDocument();
    expect(
      screen.getByText("Searching trusted source material"),
    ).toBeInTheDocument();
    const child = document.getElementById("subagent-task-task_research");
    expect(child).toHaveAttribute("data-task-id", "task_research");
    expect(child).toHaveClass("atlas-workspace-tab__item--child");
  });

  it("does not invent an Orchestrator lead when only supervisor role metadata is present", () => {
    const orchestrated = {
      ...entry({
        task_id: "task_research",
        display_title: "Research agent",
        status: "running",
        completed_at: null,
        duration_ms: null,
      }),
      parent_task_id: "main-agent",
      parent_agent_role: "orchestrator",
      parent_agent_name: "Orchestrator",
      model_display_label: "Haiku 4.5",
      current_activity: "Searching trusted source material",
    } as SubagentEntry;
    render(<AgentsTab subagents={seedSubagentMap([orchestrated])} />);

    expect(screen.queryByText("Orchestrator")).not.toBeInTheDocument();
    expect(screen.getByText("Haiku 4.5")).toBeInTheDocument();
    const child = document.getElementById("subagent-task-task_research");
    expect(child).toHaveAttribute("data-task-id", "task_research");
    expect(child).not.toHaveClass("atlas-workspace-tab__item--child");
  });
});
