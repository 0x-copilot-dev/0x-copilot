// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PendingAgentRow } from "@0x-copilot/api-types";
import { AgentFleetList } from "./AgentFleetList";

function agent(over: Partial<PendingAgentRow> = {}): PendingAgentRow {
  return {
    v: 1,
    run_id: "run_1",
    conversation_id: "conv_1",
    conversation_title: "Read issue",
    run_status: "waiting_for_approval",
    pending_count: 1,
    ...over,
  };
}

describe("AgentFleetList", () => {
  it("orders as given (running-first is the host's sort) and marks 'This run'", () => {
    const running = agent({ run_id: "run_1", run_status: "running" });
    const done = agent({
      run_id: "run_2",
      run_status: "completed",
      pending_count: 2,
    });
    render(
      <AgentFleetList
        agents={[running, done]}
        currentRunId="run_1"
        onOpenRun={() => undefined}
      />,
    );
    const rows = screen.getAllByTestId("agent-fleet-row");
    expect(rows).toHaveLength(2);
    // First row is the current run and carries the "This run" marker.
    expect(rows[0].getAttribute("data-current")).toBe("true");
    expect(screen.getByTestId("agent-fleet-this-run")).toBeInTheDocument();
  });

  it("shows a 'N waiting' pill only when a run has pending work", () => {
    render(
      <AgentFleetList
        agents={[
          agent({ run_id: "run_1", pending_count: 3 }),
          agent({ run_id: "run_2", pending_count: 0, run_status: "running" }),
        ]}
        currentRunId={null}
        onOpenRun={() => undefined}
      />,
    );
    const pills = screen.getAllByTestId("agent-fleet-waiting");
    expect(pills).toHaveLength(1);
    expect(pills[0].textContent).toBe("3 waiting");
  });

  it("fires onOpenRun with the row's agent", () => {
    const onOpenRun = vi.fn();
    const a = agent();
    render(
      <AgentFleetList agents={[a]} currentRunId={null} onOpenRun={onOpenRun} />,
    );
    screen.getByTestId("agent-fleet-row").click();
    expect(onOpenRun).toHaveBeenCalledWith(a);
  });

  it("always shows the held-work-lands-in-approvals footer note", () => {
    render(
      <AgentFleetList
        agents={[]}
        currentRunId={null}
        onOpenRun={() => undefined}
      />,
    );
    expect(screen.getByTestId("agent-fleet-note").textContent).toContain(
      "Held work from any agent lands in Approvals.",
    );
  });

  it("renders no scheduled section when the slot is absent", () => {
    render(
      <AgentFleetList
        agents={[agent()]}
        currentRunId={null}
        onOpenRun={() => undefined}
      />,
    );
    expect(screen.queryByTestId("scheduled-slot")).toBeNull();
  });

  it("renders the scheduled slot when a host supplies one", () => {
    render(
      <AgentFleetList
        agents={[agent()]}
        currentRunId={null}
        onOpenRun={() => undefined}
        scheduledSlot={<div data-testid="scheduled-slot">soon</div>}
      />,
    );
    expect(screen.getByTestId("scheduled-slot")).toBeInTheDocument();
  });
});
