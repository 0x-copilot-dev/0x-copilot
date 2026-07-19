// RunHeader — presentation tests (PR-3.5).

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RunHeader } from "./RunHeader";

describe("RunHeader", () => {
  it("renders the ACTIVE RUN kicker and the goal", () => {
    render(
      <RunHeader
        goal="Ship the renewal batch"
        mode="studio"
        onModeChange={() => {}}
      />,
    );
    expect(screen.getByTestId("run-header-kicker").textContent).toBe(
      "ACTIVE RUN",
    );
    expect(screen.getByTestId("run-header-goal").textContent).toBe(
      "Ship the renewal batch",
    );
  });

  it("falls back to idle copy when the goal is null/empty (never a blank h2, and the kicker never claims a run)", () => {
    const { rerender } = render(
      <RunHeader goal={null} mode="studio" onModeChange={() => {}} />,
    );
    // The eyebrow must NOT say "ACTIVE RUN" with no run, and the goal line is a
    // standby posture — not a duplicate of the empty-state card's copy.
    expect(screen.getByTestId("run-header-kicker").textContent).toBe("STANDBY");
    expect(screen.getByTestId("run-header-goal").textContent).toBe(
      "Standing by",
    );
    rerender(<RunHeader goal="   " mode="studio" onModeChange={() => {}} />);
    expect(screen.getByTestId("run-header-kicker").textContent).toBe("STANDBY");
    expect(screen.getByTestId("run-header-goal").textContent).toBe(
      "Standing by",
    );
  });

  it("renders a two-tab Studio/Focus segmented control reflecting the mode", () => {
    render(<RunHeader goal="G" mode="focus" onModeChange={() => {}} />);
    const tablist = screen.getByTestId("run-mode-switcher");
    expect(tablist.getAttribute("role")).toBe("tablist");
    const studio = screen.getByTestId("run-mode-studio");
    const focus = screen.getByTestId("run-mode-focus");
    expect(studio.getAttribute("aria-selected")).toBe("false");
    expect(focus.getAttribute("aria-selected")).toBe("true");
    // Roving tabindex: only the selected tab is in the tab order.
    expect(studio.getAttribute("tabindex")).toBe("-1");
    expect(focus.getAttribute("tabindex")).toBe("0");
  });

  it("fires onModeChange when a segment is clicked", () => {
    const onModeChange = vi.fn();
    render(<RunHeader goal="G" mode="studio" onModeChange={onModeChange} />);
    fireEvent.click(screen.getByTestId("run-mode-focus"));
    expect(onModeChange).toHaveBeenCalledWith("focus");
  });

  it("cycles modes with ArrowLeft/ArrowRight over the two values", () => {
    const onModeChange = vi.fn();
    render(<RunHeader goal="G" mode="studio" onModeChange={onModeChange} />);
    const tablist = screen.getByTestId("run-mode-switcher");
    fireEvent.keyDown(tablist, { key: "ArrowRight" });
    expect(onModeChange).toHaveBeenLastCalledWith("focus");
    // Wraps back to studio from focus going right.
    onModeChange.mockClear();
    render(<RunHeader goal="G" mode="focus" onModeChange={onModeChange} />);
    fireEvent.keyDown(screen.getAllByTestId("run-mode-switcher")[1], {
      key: "ArrowRight",
    });
    expect(onModeChange).toHaveBeenLastCalledWith("studio");
  });

  it("renders an optional status node beside the goal", () => {
    render(
      <RunHeader
        goal="G"
        mode="studio"
        onModeChange={() => {}}
        status={<span data-testid="probe">working</span>}
      />,
    );
    expect(screen.getByTestId("run-header-status")).not.toBeNull();
    expect(screen.getByTestId("probe").textContent).toBe("working");
  });
});
