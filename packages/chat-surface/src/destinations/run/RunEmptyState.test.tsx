// RunEmptyState — presentation tests (PR-3.11 / FR-3.25).
//
// The empty/idle cockpit is a pure goal composer: it renders the honest
// "no active run" copy + a "Give it a goal…" input, and calls `onSubmitGoal`
// with the trimmed goal on Start / Enter. It never fabricates a run — the shell
// owns what "start" does (creating the run + rebinding the cockpit).

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RunEmptyState } from "./RunEmptyState";

describe("RunEmptyState", () => {
  it("renders the honest empty copy + a goal composer (no fake run)", () => {
    render(<RunEmptyState onSubmitGoal={() => {}} />);

    expect(screen.getByTestId("run-empty-state")).not.toBeNull();
    expect(screen.getByTestId("run-empty-kicker").textContent).toBe(
      "NO ACTIVE RUN",
    );
    expect(screen.getByTestId("run-empty-title").textContent).toBe(
      "Give it a goal",
    );
    const input = screen.getByTestId(
      "run-empty-goal-input",
    ) as HTMLTextAreaElement;
    expect(input.getAttribute("placeholder")).toBe("Give it a goal…");
    // No timeline / canvas / fabricated run scaffolding.
    expect(screen.queryByTestId("thread-canvas")).toBeNull();
  });

  it("weaves the agent name into the prompt + input a11y label", () => {
    render(<RunEmptyState agentName="Mark" onSubmitGoal={() => {}} />);
    expect(screen.getByTestId("run-empty-prompt").textContent).toContain(
      "Mark will plan the steps",
    );
    expect(
      screen.getByTestId("run-empty-goal-input").getAttribute("aria-label"),
    ).toBe("Goal for Mark");
  });

  it("disables Start until a non-empty goal is entered", () => {
    render(<RunEmptyState onSubmitGoal={() => {}} />);
    const submit = screen.getByTestId("run-empty-submit") as HTMLButtonElement;
    expect(submit.disabled).toBe(true);

    fireEvent.change(screen.getByTestId("run-empty-goal-input"), {
      target: { value: "Ship the renewal batch" },
    });
    expect(submit.disabled).toBe(false);
  });

  it("fires onSubmitGoal with the trimmed goal on Start", () => {
    const onSubmitGoal = vi.fn();
    render(<RunEmptyState onSubmitGoal={onSubmitGoal} />);

    fireEvent.change(screen.getByTestId("run-empty-goal-input"), {
      target: { value: "  Draft the launch note  " },
    });
    fireEvent.click(screen.getByTestId("run-empty-submit"));

    expect(onSubmitGoal).toHaveBeenCalledTimes(1);
    expect(onSubmitGoal).toHaveBeenCalledWith("Draft the launch note");
  });

  it("submits on Enter, but Shift+Enter inserts a newline (does not submit)", () => {
    const onSubmitGoal = vi.fn();
    render(<RunEmptyState onSubmitGoal={onSubmitGoal} />);
    const input = screen.getByTestId("run-empty-goal-input");

    fireEvent.change(input, { target: { value: "Reconcile invoices" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(onSubmitGoal).not.toHaveBeenCalled();

    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSubmitGoal).toHaveBeenCalledWith("Reconcile invoices");
  });

  it("never submits a whitespace-only goal", () => {
    const onSubmitGoal = vi.fn();
    render(<RunEmptyState onSubmitGoal={onSubmitGoal} />);
    const input = screen.getByTestId("run-empty-goal-input");

    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.click(screen.getByTestId("run-empty-submit"));

    expect(onSubmitGoal).not.toHaveBeenCalled();
  });

  it("locks the composer while submitting (no double start) and shows progress", () => {
    const onSubmitGoal = vi.fn();
    render(<RunEmptyState onSubmitGoal={onSubmitGoal} submitting={true} />);
    const input = screen.getByTestId(
      "run-empty-goal-input",
    ) as HTMLTextAreaElement;
    const submit = screen.getByTestId("run-empty-submit") as HTMLButtonElement;

    expect(input.disabled).toBe(true);
    expect(submit.disabled).toBe(true);
    expect(submit.textContent).toBe("Starting…");

    // Even with text present, a submit while starting is a no-op.
    fireEvent.change(input, { target: { value: "another goal" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSubmitGoal).not.toHaveBeenCalled();
  });
});
