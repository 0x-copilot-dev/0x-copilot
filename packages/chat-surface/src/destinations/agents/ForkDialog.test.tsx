import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ForkDialog } from "./ForkDialog";

describe("<ForkDialog />", () => {
  it("renders modal dialog with warning pill and titled aria-labelledby", () => {
    render(
      <ForkDialog
        agentName="Calendar Whisperer"
        origin="system"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    const dialog = screen.getByTestId("agent-fork-dialog");
    expect(dialog.getAttribute("role")).toBe("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("aria-labelledby")).toBe(
      "agent-fork-dialog-title",
    );
    // SP-1: warning surface is the StatusPill primitive.
    expect(
      screen.getByTestId("agent-fork-dialog-warning-pill"),
    ).toHaveTextContent(/heads up/i);
    expect(screen.getByText(/calendar whisperer/i)).toBeInTheDocument();
    // Title carries the origin word; body explains "is a {origin} agent".
    expect(screen.getAllByText(/system/i).length).toBeGreaterThan(0);
  });

  it("invokes onConfirm when the single forward button is clicked", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <ForkDialog
        agentName="Slack Summarizer"
        origin="community"
        onConfirm={onConfirm}
        onCancel={onCancel}
      />,
    );
    fireEvent.click(screen.getByTestId("agent-fork-dialog-confirm"));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("invokes onCancel when the cancel button is clicked or backdrop tapped", () => {
    const onCancel = vi.fn();
    render(
      <ForkDialog
        agentName="X"
        origin="system"
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />,
    );
    fireEvent.click(screen.getByTestId("agent-fork-dialog-cancel"));
    expect(onCancel).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId("agent-fork-dialog"));
    expect(onCancel).toHaveBeenCalledTimes(2);
  });

  it("disables both buttons and shows busy copy when busy=true", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <ForkDialog
        agentName="X"
        origin="system"
        onConfirm={onConfirm}
        onCancel={onCancel}
        busy
      />,
    );
    const confirm = screen.getByTestId("agent-fork-dialog-confirm");
    expect(confirm).toBeDisabled();
    expect(confirm).toHaveTextContent(/creating your copy/i);
    expect(screen.getByTestId("agent-fork-dialog-cancel")).toBeDisabled();
    // Backdrop click is also no-op while busy.
    fireEvent.click(screen.getByTestId("agent-fork-dialog"));
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("clicks inside the panel do not bubble to the backdrop cancel", () => {
    const onCancel = vi.fn();
    render(
      <ForkDialog
        agentName="X"
        origin="community"
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />,
    );
    fireEvent.click(screen.getByTestId("agent-fork-dialog-panel"));
    expect(onCancel).not.toHaveBeenCalled();
  });
});
