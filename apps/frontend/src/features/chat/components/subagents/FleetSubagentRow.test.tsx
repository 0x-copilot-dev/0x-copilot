// PR 3.2.4 — compact fleet-row coverage.
// AC-2 (structure), AC-3 (animated progress), AC-6 (failed/cancelled status word).

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
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
});
