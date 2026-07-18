import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SaveBar, Toast } from "./SaveBar";

describe("<SaveBar>", () => {
  it("renders nothing when the section is not dirty", () => {
    render(<SaveBar dirty={false} onDiscard={vi.fn()} onSave={vi.fn()} />);
    expect(screen.queryByTestId("settings-savebar")).not.toBeInTheDocument();
  });

  it("surfaces 'Unsaved changes' with Discard / Save when dirty", () => {
    render(<SaveBar dirty onDiscard={vi.fn()} onSave={vi.fn()} />);
    const bar = screen.getByTestId("settings-savebar");
    expect(bar).toHaveAttribute("role", "region");
    expect(bar).toHaveAttribute("aria-label", "Unsaved changes");
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Discard" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
  });

  it("fires onDiscard and onSave", () => {
    const onDiscard = vi.fn();
    const onSave = vi.fn();
    render(<SaveBar dirty onDiscard={onDiscard} onSave={onSave} />);
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onDiscard).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledTimes(1);
  });

  it("disables the buttons and shows the saving label while saving", () => {
    render(<SaveBar dirty saving onDiscard={vi.fn()} onSave={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Discard" })).toBeDisabled();
  });
});

describe("<Toast>", () => {
  it("renders nothing when closed", () => {
    render(<Toast open={false} message="Export queued" />);
    expect(screen.queryByTestId("settings-toast")).not.toBeInTheDocument();
  });

  it("announces one-shot feedback via role=status (distinct from the savebar)", () => {
    render(<Toast open message="Export queued to ~/copilot/export" />);
    const toast = screen.getByTestId("settings-toast");
    expect(toast).toHaveAttribute("role", "status");
    expect(toast).toHaveAttribute("aria-live", "polite");
    expect(toast).toHaveAttribute("data-tone", "success");
    expect(
      screen.getByText("Export queued to ~/copilot/export"),
    ).toBeInTheDocument();
  });

  it("carries the requested tone and an optional dismiss control", () => {
    const onDismiss = vi.fn();
    render(
      <Toast open tone="danger" message="Key removed" onDismiss={onDismiss} />,
    );
    expect(screen.getByTestId("settings-toast")).toHaveAttribute(
      "data-tone",
      "danger",
    );
    fireEvent.click(screen.getByTestId("settings-toast-dismiss"));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("omits the dismiss control when no handler is given", () => {
    render(<Toast open message="Saved" />);
    expect(
      screen.queryByTestId("settings-toast-dismiss"),
    ).not.toBeInTheDocument();
  });
});
