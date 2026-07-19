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

  it("surfaces a start-run error instead of failing silently", () => {
    const { rerender } = render(<RunEmptyState onSubmitGoal={() => {}} />);
    // No error initially.
    expect(screen.queryByTestId("run-empty-error")).toBeNull();
    // A failure is rendered as an alert with the message.
    rerender(
      <RunEmptyState
        onSubmitGoal={() => {}}
        error={{ message: "Couldn't start the run: 500 Internal Server Error" }}
      />,
    );
    const err = screen.getByTestId("run-empty-error");
    expect(err.getAttribute("role")).toBe("alert");
    expect(err.textContent).toContain("Couldn't start the run");
  });

  it("shows the setup CTA and locks the composer when no model is configured", () => {
    const onOpenModelSettings = vi.fn();
    render(
      <RunEmptyState
        onSubmitGoal={() => {}}
        setupRequired
        onOpenModelSettings={onOpenModelSettings}
      />,
    );
    // Composer is inert — a doomed run can't be started.
    expect(
      (screen.getByTestId("run-empty-goal-input") as HTMLTextAreaElement)
        .disabled,
    ).toBe(true);
    expect(
      (screen.getByTestId("run-empty-submit") as HTMLButtonElement).disabled,
    ).toBe(true);
    // The honest setup notice + CTA is shown and opens model settings.
    expect(screen.getByTestId("run-empty-setup")).not.toBeNull();
    fireEvent.click(screen.getByTestId("run-empty-setup-cta"));
    expect(onOpenModelSettings).toHaveBeenCalledTimes(1);
  });

  it("surfaces the safe_message + an 'Add a provider key' CTA and demotes the raw envelope on a configuration error", () => {
    const onOpenModelSettings = vi.fn();
    render(
      <RunEmptyState
        onSubmitGoal={() => {}}
        onOpenModelSettings={onOpenModelSettings}
        error={{
          message:
            "Missing API key for model provider 'openai'. Add one in Settings -> Provider keys.",
          code: "configuration_error",
          correlationId: "935a40d5",
          raw: '{"detail":{"code":"configuration_error","safe_message":"Missing API key…","correlation_id":"935a40d5"}}',
        }}
      />,
    );
    // The actionable safe_message is the PRIMARY line — never the raw JSON.
    const primary = screen.getByTestId("run-empty-error-message");
    expect(primary.textContent).toContain(
      "Missing API key for model provider 'openai'",
    );
    expect(primary.textContent).not.toContain("{");
    // The config-error CTA opens Settings → Provider keys.
    fireEvent.click(screen.getByTestId("run-empty-error-cta"));
    expect(onOpenModelSettings).toHaveBeenCalledTimes(1);
    // The correlation id + raw envelope live behind a "Show details" disclosure.
    fireEvent.click(screen.getByTestId("run-empty-error-details-toggle"));
    expect(screen.getByTestId("run-empty-error-details").textContent).toContain(
      "935a40d5",
    );
  });
});
