// RunMultiSelect — presentation tests (PR-3.11 / FR-3.26).
//
// The selector renders one entry per run (goal · status · time) when the
// conversation has >1 run, and fires `onSelectRun` on click / arrow-nav. For a
// conversation with zero or one run there is no choice to make, so it renders
// NO chrome (returns null).

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RunMultiSelect } from "./RunMultiSelect";
import type { RunListItem } from "./useRunSession";

function run(overrides: Partial<RunListItem> = {}): RunListItem {
  return {
    runId: "run-a",
    goal: "Ship the renewal batch",
    status: "running",
    startedAt: "2026-05-17T10:00:00.000Z",
    ...overrides,
  };
}

const TWO_RUNS: readonly RunListItem[] = [
  run({ runId: "run-a", goal: "Ship the renewal batch", status: "running" }),
  run({
    runId: "run-b",
    goal: "Reconcile Q2 invoices",
    status: "completed",
    startedAt: "2026-05-17T09:30:00.000Z",
  }),
];

describe("RunMultiSelect", () => {
  it("renders no chrome for zero runs", () => {
    const { container } = render(
      <RunMultiSelect runs={[]} selectedRunId={null} onSelectRun={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
    expect(screen.queryByTestId("run-multi-select")).toBeNull();
  });

  it("renders no chrome for a single run (nothing to pick)", () => {
    const { container } = render(
      <RunMultiSelect
        runs={[run()]}
        selectedRunId="run-a"
        onSelectRun={() => {}}
      />,
    );
    expect(container.firstChild).toBeNull();
    expect(screen.queryByTestId("run-multi-select")).toBeNull();
  });

  it("lists each run (goal · status · time) as a tablist when >1 run", () => {
    render(
      <RunMultiSelect
        runs={TWO_RUNS}
        selectedRunId="run-a"
        onSelectRun={() => {}}
      />,
    );

    const strip = screen.getByTestId("run-multi-select");
    expect(strip.getAttribute("role")).toBe("tablist");
    expect(strip.getAttribute("aria-label")).toBe("Run selection");

    // A tab per run, each carrying goal + status + time.
    const tabA = screen.getByTestId("run-select-run-a");
    const tabB = screen.getByTestId("run-select-run-b");
    expect(tabA.getAttribute("role")).toBe("tab");
    expect(tabA.textContent).toContain("Ship the renewal batch");
    expect(screen.getByTestId("run-select-status-run-a").textContent).toBe(
      "Live",
    );
    expect(screen.getByTestId("run-select-status-run-b").textContent).toBe(
      "Done",
    );
    expect(screen.getByTestId("run-select-time-run-a")).not.toBeNull();
    expect(tabB.textContent).toContain("Reconcile Q2 invoices");
  });

  it("marks the selected run and gives it the roving tabindex", () => {
    render(
      <RunMultiSelect
        runs={TWO_RUNS}
        selectedRunId="run-b"
        onSelectRun={() => {}}
      />,
    );
    const tabA = screen.getByTestId("run-select-run-a");
    const tabB = screen.getByTestId("run-select-run-b");
    expect(tabA.getAttribute("aria-selected")).toBe("false");
    expect(tabB.getAttribute("aria-selected")).toBe("true");
    expect(tabA.getAttribute("tabindex")).toBe("-1");
    expect(tabB.getAttribute("tabindex")).toBe("0");
  });

  it("fires onSelectRun with the run id on click", () => {
    const onSelectRun = vi.fn();
    render(
      <RunMultiSelect
        runs={TWO_RUNS}
        selectedRunId="run-a"
        onSelectRun={onSelectRun}
      />,
    );
    fireEvent.click(screen.getByTestId("run-select-run-b"));
    expect(onSelectRun).toHaveBeenCalledWith("run-b");
  });

  it("cycles the selection with ArrowLeft/ArrowRight, anchored on the current run", () => {
    const onSelectRun = vi.fn();
    render(
      <RunMultiSelect
        runs={TWO_RUNS}
        selectedRunId="run-a"
        onSelectRun={onSelectRun}
      />,
    );
    const strip = screen.getByTestId("run-multi-select");

    fireEvent.keyDown(strip, { key: "ArrowRight" });
    expect(onSelectRun).toHaveBeenLastCalledWith("run-b");

    // ArrowLeft from the first run wraps to the last.
    fireEvent.keyDown(strip, { key: "ArrowLeft" });
    expect(onSelectRun).toHaveBeenLastCalledWith("run-b");
  });

  it("falls back to honest labels for a null goal / missing time", () => {
    render(
      <RunMultiSelect
        runs={[
          run({ runId: "run-a" }),
          run({ runId: "run-c", goal: null, status: null, startedAt: null }),
        ]}
        selectedRunId="run-a"
        onSelectRun={() => {}}
      />,
    );
    expect(screen.getByTestId("run-select-run-c").textContent).toContain(
      "Untitled run",
    );
    expect(screen.getByTestId("run-select-time-run-c").textContent).toBe("—");
    // Unknown status → no status label rendered.
    expect(screen.queryByTestId("run-select-status-run-c")).toBeNull();
  });
});
