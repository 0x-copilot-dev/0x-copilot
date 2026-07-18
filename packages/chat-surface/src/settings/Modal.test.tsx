import { fireEvent, render, screen } from "@testing-library/react";
import { useState, type ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";

import { Modal, StepDots, MODAL_WIDTH } from "./Modal";

describe("<StepDots>", () => {
  it("renders `total` dots and announces the current step", () => {
    render(<StepDots total={3} current={2} />);
    const group = screen.getByTestId("step-dots");
    expect(group).toHaveAttribute("aria-label", "Step 2 of 3");
    expect(group.querySelectorAll("span")).toHaveLength(3);
  });

  it("marks each dot done / active / future by the 1-based current step", () => {
    render(<StepDots total={3} current={2} />);
    const dots = screen.getByTestId("step-dots").querySelectorAll("span");
    expect(dots[0]).toHaveAttribute("data-state", "done");
    expect(dots[1]).toHaveAttribute("data-state", "active");
    expect(dots[2]).toHaveAttribute("data-state", "future");
  });

  it("advances 1 → 2 → 3 as `current` changes", () => {
    const { rerender } = render(<StepDots total={3} current={1} />);
    expect(screen.getByTestId("step-dots")).toHaveAttribute(
      "aria-label",
      "Step 1 of 3",
    );
    rerender(<StepDots total={3} current={2} />);
    expect(screen.getByTestId("step-dots")).toHaveAttribute(
      "aria-label",
      "Step 2 of 3",
    );
    rerender(<StepDots total={3} current={3} />);
    expect(screen.getByTestId("step-dots")).toHaveAttribute(
      "aria-label",
      "Step 3 of 3",
    );
  });

  it("clamps an out-of-range current step", () => {
    render(<StepDots total={3} current={9} />);
    expect(screen.getByTestId("step-dots")).toHaveAttribute(
      "aria-label",
      "Step 3 of 3",
    );
  });
});

describe("<Modal>", () => {
  it("renders nothing when closed", () => {
    render(
      <Modal open={false} onClose={() => undefined} title="Add provider key">
        <p>body</p>
      </Modal>,
    );
    expect(screen.queryByTestId("settings-modal")).not.toBeInTheDocument();
  });

  it("renders a 500px focus-trap dialog with title, mono subtitle and body", () => {
    render(
      <Modal
        open
        onClose={() => undefined}
        title="Add provider key"
        subtitle="sk-…"
      >
        <p>body</p>
      </Modal>,
    );
    const dialog = screen.getByTestId("settings-modal");
    expect(dialog).toHaveAttribute("role", "dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog.style.width).toBe(`${MODAL_WIDTH}px`);
    expect(
      screen.getByRole("heading", { name: "Add provider key" }),
    ).toBeInTheDocument();
    expect(screen.getByText("sk-…")).toBeInTheDocument();
    expect(screen.getByText("body")).toBeInTheDocument();
  });

  it("moves focus into the modal on open", () => {
    render(
      <Modal open onClose={() => undefined} title="T">
        <input data-testid="body-input" />
      </Modal>,
    );
    // First focusable descendant is the close (×) control.
    expect(screen.getByTestId("settings-modal-close")).toHaveFocus();
  });

  it("closes on Escape", () => {
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} title="T">
        <p>body</p>
      </Modal>,
    );
    fireEvent.keyDown(screen.getByTestId("settings-modal"), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on backdrop click but not on a click inside the card", () => {
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} title="T">
        <p>body</p>
      </Modal>,
    );
    fireEvent.click(screen.getByTestId("settings-modal"));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("settings-modal-scrim"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes via the × control", () => {
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} title="T" closeLabel="Close dialog">
        <p>body</p>
      </Modal>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("traps Tab focus within the modal (wraps last → first)", () => {
    render(
      <Modal open onClose={() => undefined} title="T">
        <input data-testid="body-input" />
      </Modal>,
    );
    const input = screen.getByTestId("body-input");
    input.focus();
    expect(input).toHaveFocus();
    fireEvent.keyDown(screen.getByTestId("settings-modal"), { key: "Tab" });
    // Last focusable was the input → wraps to the first (× close control).
    expect(screen.getByTestId("settings-modal-close")).toHaveFocus();
  });

  it("wraps Shift+Tab from the first focusable to the last", () => {
    render(
      <Modal open onClose={() => undefined} title="T">
        <input data-testid="body-input" />
      </Modal>,
    );
    const close = screen.getByTestId("settings-modal-close");
    close.focus();
    fireEvent.keyDown(screen.getByTestId("settings-modal"), {
      key: "Tab",
      shiftKey: true,
    });
    expect(screen.getByTestId("body-input")).toHaveFocus();
  });

  it("returns focus to the trigger when closed", () => {
    function Harness(): ReactElement {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button data-testid="trigger" onClick={() => setOpen(true)}>
            open
          </button>
          <Modal open={open} onClose={() => setOpen(false)} title="T">
            <p>body</p>
          </Modal>
        </>
      );
    }
    render(<Harness />);
    const trigger = screen.getByTestId("trigger");
    // fireEvent.click does not move focus; focus the trigger first so the
    // modal captures it as the element to restore to on close.
    trigger.focus();
    expect(trigger).toHaveFocus();
    fireEvent.click(trigger);
    expect(screen.getByTestId("settings-modal-close")).toHaveFocus();
    fireEvent.keyDown(screen.getByTestId("settings-modal"), { key: "Escape" });
    expect(trigger).toHaveFocus();
  });

  it("renders the footer slot (StepDots + actions)", () => {
    render(
      <Modal
        open
        onClose={() => undefined}
        title="Add provider key"
        footer={
          <>
            <StepDots total={3} current={1} />
            <button>Next</button>
          </>
        }
      >
        <p>body</p>
      </Modal>,
    );
    expect(screen.getByTestId("step-dots")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeInTheDocument();
  });
});
