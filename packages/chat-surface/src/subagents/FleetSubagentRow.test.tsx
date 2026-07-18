// PR 3.2.4 — compact fleet-row coverage. Moved down with the component
// (PR-1.5); the same assertions run from chat-surface.
// AC-2 (structure), AC-3 (animated progress), AC-6 (failed/cancelled status word).
// PR 3.2.7 — paused chrome (amber indicator + paused chip + frozen
// progress) + clickable row with inline timeline disclosure + jump-to-
// approval link.

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { SubagentActivityRecord } from "./subagentHelpers";
import { FleetSubagentRow } from "./FleetSubagentRow";
import type { SubagentCardViewModel } from "./subagentCardViewModel";

function vm(
  overrides: Partial<SubagentCardViewModel> = {},
): SubagentCardViewModel {
  return {
    taskId: "task_doc_reader",
    name: "Doc reader",
    status: "running",
    terminal: false,
    task: "Read positioning + GTM plan, extract claims",
    finding: null,
    fullResult: null,
    startedAt: "2026-05-07T10:00:00Z",
    completedAt: null,
    durationMs: null,
    isError: false,
    ...overrides,
  };
}

describe("FleetSubagentRow", () => {
  it("renders name + task + progress bar + elapsed for a running subagent", () => {
    render(<FleetSubagentRow view={vm()} progress={0.45} />);
    expect(screen.getByText("Doc reader")).toBeInTheDocument();
    expect(
      screen.getByText("Read positioning + GTM plan, extract claims"),
    ).toBeInTheDocument();
    const progress = screen.getByRole("progressbar");
    expect(progress.getAttribute("aria-valuenow")).toBe("0.45");
  });

  it("does not render a disclosure or jump button (compact row only)", () => {
    const { container } = render(<FleetSubagentRow view={vm()} />);
    expect(container.querySelector("details")).toBeNull();
    expect(container.querySelector("button")).toBeNull();
  });

  it("freezes progress at 1 when terminal", () => {
    render(
      <FleetSubagentRow
        view={vm({ status: "completed", terminal: true, durationMs: 18000 })}
      />,
    );
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe(
      "1",
    );
  });

  it("renders the duration label when terminal", () => {
    render(
      <FleetSubagentRow
        view={vm({ status: "completed", terminal: true, durationMs: 18000 })}
      />,
    );
    expect(screen.getByText("18s")).toBeInTheDocument();
  });

  it("renders failed status word inline when failed", () => {
    render(
      <FleetSubagentRow
        view={vm({
          status: "failed",
          terminal: true,
          durationMs: 4200,
          isError: true,
        })}
      />,
    );
    expect(screen.getByText(/4\.2s · failed/)).toBeInTheDocument();
  });

  it("renders cancelled status word inline when cancelled", () => {
    render(
      <FleetSubagentRow
        view={vm({
          status: "cancelled",
          terminal: true,
          durationMs: 12000,
        })}
      />,
    );
    expect(screen.getByText(/12s · cancelled/)).toBeInTheDocument();
  });

  it("encodes status in [data-status] for CSS animation hooks", () => {
    const { container, rerender } = render(<FleetSubagentRow view={vm()} />);
    expect(container.firstChild).toHaveAttribute("data-status", "running");
    rerender(
      <FleetSubagentRow
        view={vm({ status: "completed", terminal: true, durationMs: 1000 })}
      />,
    );
    expect(container.firstChild).toHaveAttribute("data-status", "completed");
  });

  it("omits the task line when not provided", () => {
    render(<FleetSubagentRow view={vm({ task: null })} />);
    expect(screen.getByText("Doc reader")).toBeInTheDocument();
    expect(
      screen.queryByText("Read positioning + GTM plan, extract claims"),
    ).not.toBeInTheDocument();
  });

  it("renders paused chrome when status is paused", () => {
    const { container } = render(
      <FleetSubagentRow
        view={vm({ status: "paused", pauseReason: "approval" })}
      />,
    );
    const row = container.querySelector(".subagent-fleet-row");
    expect(row).toHaveAttribute("data-status", "paused");
    expect(row).toHaveAttribute("data-paused", "true");
    expect(
      screen.getByLabelText("Paused, waiting on approval"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Paused · waiting on approval/),
    ).toBeInTheDocument();
  });

  it("uses the right reason copy for mcp_auth", () => {
    render(
      <FleetSubagentRow
        view={vm({ status: "paused", pauseReason: "mcp_auth" })}
      />,
    );
    expect(
      screen.getByText(/Paused · waiting on connector/),
    ).toBeInTheDocument();
  });

  it("uses the right reason copy for ask_a_question", () => {
    render(
      <FleetSubagentRow
        view={vm({ status: "paused", pauseReason: "ask_a_question" })}
      />,
    );
    expect(screen.getByText(/Paused · waiting for answer/)).toBeInTheDocument();
  });

  it("freezes progress at the last reported value when paused", () => {
    const { container } = render(
      <FleetSubagentRow
        view={vm({ status: "paused", pauseReason: "approval" })}
        progress={0.32}
      />,
    );
    const fill = container.querySelector(
      ".subagent-fleet-row__progress-fill",
    ) as HTMLElement | null;
    expect(fill?.getAttribute("style")).toContain("scaleX(0.32)");
  });

  it("toggles the inline timeline on click and back on second click", () => {
    const { container } = render(<FleetSubagentRow view={vm()} />);
    const row = container.querySelector(
      ".subagent-fleet-row",
    ) as HTMLElement | null;
    expect(row).not.toBeNull();
    expect(
      container.querySelector(".subagent-fleet-row__inline-timeline"),
    ).toBeNull();
    fireEvent.click(row!);
    expect(
      container.querySelector(".subagent-fleet-row__inline-timeline"),
    ).not.toBeNull();
    expect(row).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(row!);
    expect(
      container.querySelector(".subagent-fleet-row__inline-timeline"),
    ).toBeNull();
    expect(row).toHaveAttribute("aria-expanded", "false");
  });

  it("expands on Enter and Space keypresses", () => {
    const { container } = render(<FleetSubagentRow view={vm()} />);
    const row = container.querySelector(
      ".subagent-fleet-row",
    ) as HTMLElement | null;
    fireEvent.keyDown(row!, { key: "Enter" });
    expect(row).toHaveAttribute("aria-expanded", "true");
    fireEvent.keyDown(row!, { key: " " });
    expect(row).toHaveAttribute("aria-expanded", "false");
  });

  it("each row maintains independent disclosure state", () => {
    const { container } = render(
      <div>
        <FleetSubagentRow view={vm({ taskId: "a", name: "Doc A" })} />
        <FleetSubagentRow view={vm({ taskId: "b", name: "Doc B" })} />
      </div>,
    );
    const rows = container.querySelectorAll(".subagent-fleet-row");
    fireEvent.click(rows[0]);
    expect(rows[0]).toHaveAttribute("aria-expanded", "true");
    expect(rows[1]).toHaveAttribute("aria-expanded", "false");
  });

  it("renders provided activities in the inline timeline when expanded", () => {
    const activities: readonly SubagentActivityRecord[] = [
      {
        id: "act_1",
        kind: "tool",
        title: "Search documents",
        summary: "search('positioning')",
        status: "completed",
        timestamp: "2026-05-07T10:00:01Z",
      } as unknown as SubagentActivityRecord,
    ];
    const { container } = render(
      <FleetSubagentRow view={vm()} activities={activities} />,
    );
    fireEvent.click(container.querySelector(".subagent-fleet-row")!);
    expect(screen.getByText(/Search [Dd]ocuments/)).toBeInTheDocument();
  });

  it("falls back to a calm empty hint when there are no activities", () => {
    const { container } = render(<FleetSubagentRow view={vm()} />);
    fireEvent.click(container.querySelector(".subagent-fleet-row")!);
    expect(screen.getByText(/No activity yet/)).toBeInTheDocument();
  });

  it("shows the Review approval link only when paused with a source_event_id and onJumpToApproval", () => {
    const onJump = vi.fn();
    const { container, rerender } = render(
      <FleetSubagentRow
        view={vm({ status: "paused", pauseReason: "approval" })}
        onJumpToApproval={onJump}
      />,
    );
    fireEvent.click(container.querySelector(".subagent-fleet-row")!);
    expect(screen.queryByText(/Review approval →/)).toBeNull();

    rerender(
      <FleetSubagentRow
        view={vm({
          status: "paused",
          pauseReason: "approval",
          pauseSourceEventId: "evt_42",
        })}
        onJumpToApproval={onJump}
      />,
    );
    const link = screen.getByText(/Review approval →/);
    fireEvent.click(link);
    expect(onJump).toHaveBeenCalledWith("evt_42");
  });

  it("shows the right jump-link copy for connector-auth pauses", () => {
    const { container } = render(
      <FleetSubagentRow
        view={vm({
          status: "paused",
          pauseReason: "mcp_auth",
          pauseSourceEventId: "evt_99",
        })}
        onJumpToApproval={() => undefined}
      />,
    );
    fireEvent.click(container.querySelector(".subagent-fleet-row")!);
    expect(screen.getByText(/Review connector auth →/)).toBeInTheDocument();
  });

  it("does not bubble the jump-link click to the row toggle", () => {
    const { container } = render(
      <FleetSubagentRow
        view={vm({
          status: "paused",
          pauseReason: "approval",
          pauseSourceEventId: "evt_42",
        })}
        onJumpToApproval={() => undefined}
      />,
    );
    const row = container.querySelector(
      ".subagent-fleet-row",
    ) as HTMLElement | null;
    fireEvent.click(row!);
    expect(row).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(screen.getByText(/Review approval →/));
    // Still expanded — the row's onClick must not have re-fired.
    expect(row).toHaveAttribute("aria-expanded", "true");
  });
});
