import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RunTerminalBeatCard } from "./RunTerminalBeatCard";
import type { RunTerminalBeat } from "./runTerminalBeat";

const retryable: RunTerminalBeat = {
  title: "Run interrupted",
  copy: "0xCopilot stopped this run because the worker became unresponsive.",
  code: "RUN_WORKER_LOST",
  retryable: true,
  status: "failed",
};

describe("RunTerminalBeatCard", () => {
  it("offers the re-run and shows the goal it would send", () => {
    const onStartNewRun = vi.fn();
    render(
      <RunTerminalBeatCard
        beat={retryable}
        goal="Reconcile the July invoices"
        onStartNewRun={onStartNewRun}
      />,
    );

    expect(screen.getByText("Run interrupted")).toBeInTheDocument();
    // The goal is visible so the button is never a silent decision about what
    // is about to run.
    expect(screen.getByText("Reconcile the July invoices")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Start a new run with this goal" }),
    );
    expect(onStartNewRun).toHaveBeenCalledOnce();
  });

  it("draws no action when the runtime says a retry cannot help", () => {
    render(
      <RunTerminalBeatCard
        beat={{
          ...retryable,
          title: "Not allowed",
          code: "PERMISSION_DENIED",
          retryable: false,
        }}
        goal="Delete the archived channels"
        onStartNewRun={vi.fn()}
      />,
    );

    expect(screen.getByText("Not allowed")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    // No goal chip either — nothing is going to be re-sent.
    expect(screen.queryByText("Delete the archived channels")).toBeNull();
  });

  it("draws no action when the host supplies no dispatcher", () => {
    render(<RunTerminalBeatCard beat={retryable} goal="Some goal" />);

    expect(screen.queryByRole("button")).toBeNull();
  });

  it("disables the action while a run is already starting", () => {
    render(
      <RunTerminalBeatCard
        beat={retryable}
        goal="Some goal"
        onStartNewRun={vi.fn()}
        starting
      />,
    );

    expect(screen.getByRole("button", { name: "Starting…" })).toBeDisabled();
  });
});
