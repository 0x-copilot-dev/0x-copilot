import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AgentActivityRow } from "./AgentActivityRow";
import type { AgentActivityRowViewModel } from "./agentActivityRowViewModel";
import type { SubagentActivityRecord } from "./subagentHelpers";

function view(
  overrides: Partial<AgentActivityRowViewModel> = {},
): AgentActivityRowViewModel {
  return {
    taskId: "task_research",
    name: "Research agent",
    status: "running",
    terminal: false,
    task: "Find primary sources",
    finding: null,
    fullResult: null,
    startedAt: "2026-05-06T10:00:00Z",
    completedAt: null,
    durationMs: null,
    isError: false,
    parentTaskId: "main-agent",
    parentAgentRole: "orchestrator",
    parentAgentName: "Orchestrator",
    modelDisplayLabel: "Haiku 4.5",
    currentActivity: "Searching trusted source material",
    ...overrides,
  };
}

function activity(): SubagentActivityRecord {
  return {
    id: "call-1",
    kind: "tool",
    title: "web.search",
    status: "completed",
    summary: "3 primary sources",
    inputSummary: null,
    result: null,
    isError: false,
  };
}

describe("AgentActivityRow", () => {
  it("renders the quiet Focus scan recipe without a wordy lifecycle pill", () => {
    const { container } = render(
      <AgentActivityRow view={view()} activities={[]} depth={1} />,
    );

    expect(screen.getByText("Research agent")).toHaveClass(
      "agent-activity-row__name",
    );
    expect(screen.getByText("Haiku 4.5")).toHaveClass(
      "agent-activity-row__model",
    );
    expect(screen.getByText("Searching trusted source material")).toHaveClass(
      "agent-activity-row__activity",
    );
    expect(screen.getByRole("img", { name: "Running" })).toHaveClass(
      "agent-activity-row__lifecycle",
    );
    expect(screen.queryByText("RUNNING")).not.toBeInTheDocument();
    expect(container.querySelector(".agent-activity-row")).toHaveAttribute(
      "data-depth",
      "1",
    );
  });

  it("reveals and hides the timeline through a compact native disclosure", async () => {
    const user = userEvent.setup();
    render(<AgentActivityRow view={view()} activities={[activity()]} />);

    const details = screen.getByTestId(
      "agent-activity-row-details-task_research",
    ) as HTMLDetailsElement;
    expect(details.open).toBe(false);
    await user.click(details.querySelector("summary")!);
    expect(details.open).toBe(true);
    expect(
      screen.getByRole("region", { name: "Research agent activity details" }),
    ).toHaveTextContent("3 primary sources");
    await user.click(details.querySelector("summary")!);
    expect(details.open).toBe(false);
  });

  it("keeps parent rows scan-only", () => {
    render(
      <AgentActivityRow
        view={view({ name: "Orchestrator" })}
        activities={[]}
        lead
      />,
    );
    expect(
      screen.queryByTestId("agent-activity-row-details-task_research"),
    ).not.toBeInTheDocument();
  });
});
