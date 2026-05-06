// PR 3.2.1 — disclosure UX coverage for the Agents tab.
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
} from "@enterprise-search/api-types";
import {
  emptySubagentMap,
  seedSubagentMap,
} from "../../chatModel/subagentReducer";
import type { SubagentActivityRecord } from "../../utils/activityDataBuilders";
import { AgentsTab } from "./AgentsTab";

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

describe("AgentsTab disclosure", () => {
  it("renders the empty hint when no subagents have been dispatched", () => {
    render(<AgentsTab subagents={emptySubagentMap()} />);
    expect(
      screen.getByText(
        /Subagents run here when Atlas dispatches parallel work/,
      ),
    ).toBeInTheDocument();
  });

  it("renders one details disclosure per subagent, closed by default", () => {
    const subagents = seedSubagentMap([entry()]);
    render(<AgentsTab subagents={subagents} />);
    const details = screen.getByTestId("subagent-card-details-task_doc_reader");
    expect(details).toBeInstanceOf(HTMLDetailsElement);
    expect((details as HTMLDetailsElement).open).toBe(false);
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
    expect(screen.getByText("Doc Reader")).toBeInTheDocument();
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
    // Closed: timeline rows are present in the DOM (browsers preserve
    // <details> children) but the user agent treats them as collapsed.
    // Open the disclosure by clicking the summary.
    const summary = screen.getByText(/Completed in 18s/);
    await user.click(summary);
    const details = screen.getByTestId(
      "subagent-card-details-task_doc_reader",
    ) as HTMLDetailsElement;
    expect(details.open).toBe(true);
    expect(
      details.querySelectorAll(".aui-tool-card__timeline-item").length,
    ).toBe(2);
    expect(details).toHaveTextContent("4 hits");
    expect(details).toHaveTextContent("GTM/FY26-Q1 plan");
  });

  it("auto-opens the focused subagent's disclosure on first render", () => {
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
      "subagent-card-details-task_doc_reader",
    ) as HTMLDetailsElement;
    expect(details.open).toBe(true);
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
    await user.click(screen.getByText(/Completed in 18s/));
    expect(
      screen.getByText(/Single-shot response — no inner tool calls\./),
    ).toBeInTheDocument();
  });

  it("composes the workspace-narrow timeline class on top of the in-thread base class", () => {
    const subagents = seedSubagentMap([entry()]);
    const { container } = render(
      <AgentsTab
        subagents={subagents}
        activitiesByTask={activityMap({
          task_doc_reader: [activity()],
        })}
      />,
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
      // PR 3.2.2 — `subagentCardFromEntry` runs the subagent_name through
      // `formatAgentName`, so "doc_reader" → "Doc reader" in the aria-label.
      screen.getByRole("button", { name: /Open Doc Reader in thread/ }),
    );
    expect(onJumpToSubagent).toHaveBeenCalledOnce();
  });

  it("renders a danger badge for failed subagents", () => {
    const status: SubagentLifecycleStatus = "failed";
    const subagents = seedSubagentMap([entry({ status, duration_ms: 4200 })]);
    render(<AgentsTab subagents={subagents} />);
    // Badge content is the user-facing label.
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });

  it("shows working… for in-flight subagents inside the disclosure summary", () => {
    const subagents = seedSubagentMap([
      entry({ status: "running", duration_ms: null, completed_at: null }),
    ]);
    render(<AgentsTab subagents={subagents} />);
    expect(screen.getByText(/working…/)).toBeInTheDocument();
  });
});
